import json
import logging
from datetime import datetime
from pprint import pprint
import asyncio

from sortedcontainers import SortedDict
from okx_async.websocket.WsPublicAsync import WsPublicAsync


class OkxOrderBook:
    """OKX order book class

    Maintains an in-memory order book for a trading pair from OKX exchange.

    The order book is stored as a dict with 'asks' and 'bids' fields, each being
    a SortedDict mapping price levels (as float keys) to volumes (as strings).
    Using SortedDict ensures O(log n) insertion/deletion and O(1) access to
    best bid/ask, eliminating the need for repeated sorting.

    This class handles websocket connections, subscriptions, and real-time updates
    to maintain an accurate representation of the order book.

    Attributes:
        sym: Trading symbol (e.g., 'BTC-USDT')
        uquote: Quote currency unit
        url: Websocket URL for OKX
        verbose: Enable verbose logging
        book: Dict containing 'asks' (sorted ascending) and 'bids' (sorted descending) order book data
        ws: Websocket connection instance
        connection_id: OKX connection identifier
        initialized: Whether a snapshot has been received
    """

    # Class-level function for descending sort (used for bids)
    @staticmethod
    def _descending_key(x):
        """Key function for sorting bids in descending order."""
        return -x

    def __init__(self, sym, uquote, url, verbose=False, logger=None):
        """Initialize OKX order book.

        Args:
            sym: Trading symbol in format 'BASE-QUOTE' (e.g., 'BTC-USDT')
            uquote: Quote currency unit
            url: Websocket URL for OKX connection
            verbose: Enable verbose logging (default: False)
            logger: Logger instance to use. If None, creates a default logger (default: None)

        Raises:
            ValueError: If symbol format is invalid
        """
        self.sym = sym
        bq = self.bq()
        if len(bq) != 2:
            raise ValueError("Symbol not valid. Must be of form <base>-<quote>")
        self.uquote = uquote
        self.ws = None
        self.connection_id = None
        self.verbose = verbose
        self.book = {}
        self.url = url
        self.initialized = False
        self._lock = asyncio.Lock()
        self.logger = logger or logging.getLogger(__name__)

    def bq(self):
        """Split symbol into base and quote currencies.

        Returns:
            List of [base, quote] currency strings
        """
        return self.sym.split("-")

    async def connect(self):
        """Connect to OKX websocket.

        Establishes connection to the OKX public websocket endpoint.
        """
        self.logger.info(f"Connecting to OKX websocket at {self.url}")
        self.ws = WsPublicAsync(url=self.url)
        await self.ws.start()

    async def subscribe(self):
        """Subscribe to order book websocket channel.

        Subscribes to the 'books' channel for the configured trading symbol.
        """
        self.logger.info("Subscribing to OKX order book")
        await self.ws.subscribe([{"channel": "books", "instId": self.sym}], self)
        self.logger.info("Subscribed to OKX order book")

    def print(self):
        """Print the current order book to console in a clean format."""
        print("Order book:")
        # Convert to list of tuples for clean printing with proper ordering
        # Asks: sort descending (highest to lowest) - away from best ask
        asks_list = list(self.book.get("asks", {}).items())
        asks_list.sort(key=lambda x: x[0], reverse=True)

        # Bids: sort descending (highest to lowest) - best bid first
        bids_list = list(self.book.get("bids", {}).items())
        bids_list.sort(key=lambda x: x[0], reverse=True)

        clean_book = {"asks": asks_list, "bids": bids_list}
        pprint(clean_book)

    def __call__(self, message):
        """Handle incoming websocket messages.

        Processes subscription confirmations, snapshots, and updates from OKX.
        Updates the internal order book representation accordingly.

        Args:
            message: JSON string message from OKX websocket

        Raises:
            ValueError: If an unknown event is received
            Exception: If an error is returned or unknown action is received

        Reference:
            https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel
        """
        message = json.loads(message)

        if "event" in message:
            if message["event"] == "subscribe":
                # Initialize feed data
                self.starttime = datetime.utcnow()
                self.connection_id = message["connId"]
                self.logger.info(f"Subscribed to {self.sym} order book on OKX")
                self.logger.info(f"  Connection ID: {self.connection_id}")
                self.logger.info(
                    f"  Start time (UTC): {self.starttime.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                raise ValueError(
                    f"Unknown event {message['event']} returned in callback"
                )

        elif "error" in message:
            raise Exception(f"Callback returned an error: {message['error']}")

        elif "action" in message:
            if message["action"] == "snapshot":
                self.logger.info("ORDER BOOK SNAPSHOT received")
                if len(message["data"]) > 1:
                    self.logger.warning("More than one order book snapshot received")

                for d in message["data"]:
                    # Asks sorted ascending (lowest first)
                    self.book["asks"] = SortedDict()
                    for depth in d["asks"]:
                        price_float = float(depth[0])
                        self.book["asks"][price_float] = depth[1]

                    # Bids sorted descending (highest first)
                    self.book["bids"] = SortedDict(self._descending_key)
                    for depth in d["bids"]:
                        price_float = float(depth[0])
                        self.book["bids"][price_float] = depth[1]

                self.initialized = True

            elif message["action"] == "update":
                if self.verbose:
                    self.logger.debug(f"ORDER BOOK UPDATE received: {message['data']}")
                if len(message["data"]) > 1:
                    self.logger.warning("More than one order book update received")

                for d in message["data"]:
                    for side in ["asks", "bids"]:
                        if side not in self.book:
                            # Initialize if not exists
                            if side == "asks":
                                self.book[side] = SortedDict()
                            else:
                                self.book[side] = SortedDict(self._descending_key)

                        for depth in d[side]:
                            price_float = float(depth[0])
                            if float(depth[1]) > 0:
                                self.book[side][price_float] = depth[1]
                            else:
                                # Remove price level if volume is 0
                                self.book[side].pop(price_float, None)

                    # Validate order book depth
                    asks_depth = len(self.book.get("asks", {}))
                    bids_depth = len(self.book.get("bids", {}))

                    if asks_depth != 400:
                        self.logger.warning(
                            f"OKX order book ask depth is {asks_depth}, expected 400"
                        )
                    if bids_depth != 400:
                        self.logger.warning(
                            f"OKX order book bid depth is {bids_depth}, expected 400"
                        )

            else:
                raise Exception(
                    f"Unknown action {message['action']} returned in callback"
                )

    def mid_price(self) -> float:
        """Calculate the mid price of the order book.

        The mid price is the average of the lowest ask and highest bid.
        With SortedDict, this is an O(1) operation accessing the first elements.

        Returns:
            Mid price as float, or None if order book is not initialized
            or is empty
        """
        if not self.initialized:
            return None

        if not self.book or "asks" not in self.book or "bids" not in self.book:
            return None

        if not self.book["asks"] or not self.book["bids"]:
            return None

        try:
            # O(1) access to best prices with SortedDict
            lowest_ask = self.book["asks"].keys()[0]  # First key in ascending order
            highest_bid = self.book["bids"].keys()[0]  # First key in descending order
            return (lowest_ask + highest_bid) / 2
        except (ValueError, KeyError, IndexError):
            return None

    def price(
        self, direction: str, amount: float, bq_toggle: bool
    ) -> tuple[float, float, float]:
        """Calculate price at which an amount of currency can be bought or sold.

        Returns average and max/min price at which amount will get bought/sold
        and corresponding amount.

        If order book isn't deep enough to cover requested amount, prices and
        amount returned reflect all liquidity in order book being used up.

        Note: size and amount values are in currency given by bq_toggle.
        volume is always in base currency.

        Args:
            direction: "buy" or "sell"
            amount: Amount to buy or sell
            bq_toggle: Whether amount is measured in base (True) or quote (False) currency

        Returns:
            Tuple of (price, level, size) where:
                - price: Average price at which amount will be bought or sold
                - level: Max/min price at which amount will be bought/sold
                - size: Amount that will be bought or sold (in currency given by bq_toggle)
            Returns (None, None, None) if order book is not ready

        Raises:
            ValueError: If direction is not 'buy' or 'sell'
        """
        if not self.initialized:
            return None, None, None

        if not self.book or "asks" not in self.book or "bids" not in self.book:
            return None, None, None

        def dicttofloat(data):
            """Convert dict item to float for sorting."""
            return float(data[0])

        def volume_to_size(volume, price, bq_toggle):
            """Return amount of base or quote currency equivalent to given base currency volume.

            Args:
                volume: Volume in base currency
                price: Price level
                bq_toggle: True for base currency, False for quote currency

            Returns:
                Size in the currency specified by bq_toggle
            """
            return volume if bq_toggle else price * volume

        def size_to_volume(size, price, bq_toggle):
            """Return amount of base currency equivalent to given size.

            Args:
                size: Size in currency specified by bq_toggle
                price: Price level
                bq_toggle: True if size is in base currency, False for quote currency

            Returns:
                Volume in base currency
            """
            return size if bq_toggle else size / price

        if direction == "buy":
            side = "asks"
        elif direction == "sell":
            side = "bids"
        else:
            raise ValueError(
                f"Unknown direction '{direction}'. Must be 'buy' or 'sell'"
            )

        if not self.book.get(side):
            return None, None, None

        # No sorting needed - SortedDict maintains order automatically
        # Asks are already sorted ascending, bids are already sorted descending
        size = 0  # In same currency as amount (as given by bq_toggle)
        price = None
        level = None

        for price_key, v in self.book[side].items():
            level = price_key  # Already a float
            volume = min(
                float(v), size_to_volume(amount - size, level, bq_toggle)
            )  # NOTE: volume is always base currency

            if price is None:
                price = level
                size = volume_to_size(volume, level, bq_toggle)
            else:
                price = (
                    price * size_to_volume(size, price, bq_toggle) + level * volume
                ) / (size_to_volume(size, price, bq_toggle) + volume)
                size += volume_to_size(volume, level, bq_toggle)

            if size >= amount:
                break

        return price, level, size
