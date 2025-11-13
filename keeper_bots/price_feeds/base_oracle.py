"""
Base Oracle class and utilities shared across all price feeds.
"""
import asyncio
import random
import time
import statistics
import aiohttp
import math
import logging

# Coinbase for USDT-USD conversion
COINBASE_API_URL = "https://api.coinbase.com/v2"
USDT_USD_PAIR = "USDT-USD"


class Oracle:
    """
    Base Oracle class for computing volume-weighted average price (VWAP) from trade data.
    """
    def __init__(self, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize Oracle with configurable trading pairs.

        Args:
            trading_pairs: List of trading pairs, e.g., ["XCH-USDT"]
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds (time before prices are considered valid)
            min_notional: Minimum notional value for trades
        """
        self.trading_pairs = trading_pairs
        self.window = window_sec
        self.startup_window = startup_window_sec
        self.min_notional = min_notional
        self.trades = []  # list of (ts_ms, px, qty)
        self.last_pub = 0
        self.last_trade_ts = 0
        self.last_price = float("nan")
        self.usdt_usd_price = float("nan")
        self.start_time = None  # Will be set when first connected

        # Extract base currency for logging (assumes all pairs have same base)
        # Handle both dash and underscore separators
        separator = "-" if "-" in trading_pairs[0] else "_"
        self.base_currency = trading_pairs[0].split(separator)[0] if trading_pairs else "UNKNOWN"

    def set_usdt_usd_price(self, price):
        """Set the USDT-USD conversion rate."""
        if price and price > 0:
            self.usdt_usd_price = price

    def update_parameters(self, window_sec=None, startup_window_sec=None, min_notional=None):
        """
        Update Oracle parameters dynamically.

        Args:
            window_sec: New time window for trades in seconds (optional)
            startup_window_sec: New startup window length in seconds (optional)
            min_notional: New minimum notional value for trades (optional)
        """
        # Validate parameters
        if window_sec is not None:
            if not isinstance(window_sec, (int, float)) or window_sec <= 0:
                raise ValueError(f"window_sec must be a positive number, got: {window_sec}")

            old_window = self.window
            self.window = window_sec

            # Clean up trades outside the new window if it's smaller
            if window_sec < old_window:
                now = int(time.time() * 1000)
                cutoff = now - self.window * 1000
                # Remove trades older than new window
                original_count = len(self.trades)
                self.trades = [trade for trade in self.trades if trade[0] >= cutoff]
                removed_count = original_count - len(self.trades)
                if removed_count > 0:
                    logging.info("Window reduced from %ds to %ds, removed %d old trades",
                               old_window, window_sec, removed_count)

        if startup_window_sec is not None:
            if not isinstance(startup_window_sec, (int, float)) or startup_window_sec < 0:
                raise ValueError(f"startup_window_sec must be a non-negative number, got: {startup_window_sec}")
            self.startup_window = startup_window_sec

        if min_notional is not None:
            if not isinstance(min_notional, (int, float)) or min_notional < 0:
                raise ValueError(f"min_notional must be a non-negative number, got: {min_notional}")
            self.min_notional = min_notional

    def add_trade(self, pair, ts_ms, px, qty):
        """
        Add a trade to the oracle.

        Args:
            pair: Trading pair (e.g., "XCH-USDT" or "XCH_USDT")
            ts_ms: Timestamp in milliseconds
            px: Price
            qty: Quantity
        """
        # Convert price to USD if necessary
        usd_px = px
        if pair.endswith("-USDT") or pair.endswith("_USDT"):
            if not math.isnan(self.usdt_usd_price):
                usd_px = px * self.usdt_usd_price
            else:
                # Can't convert, so we skip this trade
                return

        if usd_px * qty < self.min_notional:
            return
        now = int(time.time() * 1000)
        self.trades.append((ts_ms, usd_px, qty))
        # drop old
        cutoff = now - self.window * 1000
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.pop(0)
        self.last_trade_ts = ts_ms

    def compute(self, fallback_mid=None):
        """
        Compute the volume-weighted average price (VWAP).

        Args:
            fallback_mid: Fallback price to use if no trades available

        Returns:
            Tuple of (price, metadata)
        """
        now = int(time.time() * 1000)

        # Check if we're still in startup window
        if self.start_time is not None:
            startup_elapsed = (now / 1000.0) - self.start_time
            if startup_elapsed < self.startup_window:
                # Still in startup window - return NaN
                meta = {"stale": False, "window": self.window, "trades": len(self.trades), "startup": True}
                return float("nan"), meta

        # stale?
        stale = (now - self.last_trade_ts) > 5000
        price = None
        meta = {"stale": stale, "window": self.window, "trades": len(self.trades), "startup": False}
        if self.trades:
            vol = sum(q for _, _, q in self.trades)
            if vol > 0:
                vwap = sum(px * q for _, px, q in self.trades) / vol
                # outlier trim (simple): drop if far from median of trade prices
                med = statistics.median([px for _, px, _ in self.trades])
                if abs(vwap - med) / med > 0.03 and len(self.trades) > 4:
                    # trim extremes
                    prices = sorted([px for _, px, _ in self.trades])
                    core = prices[len(prices) // 10 : -len(prices) // 10 or None]
                    vwap = sum(p * q for (_, p, q) in self.trades if p in core) / sum(
                        q for (_, p, q) in self.trades if p in core
                    )
                price = vwap
        if price is None:
            price = fallback_mid if fallback_mid is not None else self.last_price
            meta["degraded"] = True
        self.last_price = price
        meta["ts"] = now
        return price, meta


async def usdt_price_fetcher(oracle: Oracle):
    """
    Fetches USDT-USD price from Coinbase and updates the oracle.

    Args:
        oracle: Oracle instance to update with USDT-USD price
    """
    base_delay = 5.0
    max_delay = 60.0
    delay = base_delay

    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{COINBASE_API_URL}/prices/{USDT_USD_PAIR}/spot"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        price = float(data["data"]["amount"])
                        oracle.set_usdt_usd_price(price)
                        delay = base_delay  # Reset backoff delay on success
                        await asyncio.sleep(15)  # Wait for the normal interval
                        continue

                    # Handle non-200 server responses
                    body = await response.text()
                    logging.warning(
                        "Failed to fetch USDT-USD price from Coinbase (Status: %s). Retrying in %.2fs. Body: %s",
                        response.status,
                        delay,
                        body,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.error("Error fetching USDT-USD price: %s. Retrying in %.2fs.", e, delay)

        await asyncio.sleep(delay)
        delay = min(max_delay, delay * 1.5 + random.uniform(0, 1))  # Exponential backoff with jitter


class BaseOracleFeed:
    """
    Base class for exchange-specific oracle feeds.
    Subclasses must implement the _create_websocket_task method.
    """
    def __init__(self, exchange_name, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize the base oracle feed.

        Args:
            exchange_name: Name of the exchange (for logging)
            trading_pairs: List of trading pairs
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds
            min_notional: Minimum notional value for trades
        """
        self.exchange_name = exchange_name
        self.oracle = Oracle(trading_pairs, window_sec, startup_window_sec, min_notional)
        self._tasks = []
        self.book_mids = {}  # Store current book mid prices for fallback

    async def __aenter__(self):
        """Start the feed tasks."""
        logging.info("Starting %s Oracle Feed...", self.exchange_name)
        self._tasks = [
            asyncio.create_task(self._create_websocket_task()),
            asyncio.create_task(usdt_price_fetcher(self.oracle))
        ]
        # Give it a moment to connect and start receiving data
        await asyncio.sleep(2)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Stop the feed tasks."""
        logging.info("Stopping %s Oracle Feed...", self.exchange_name)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def get_price(self):
        """Get the current price from the oracle."""
        # Calculate volume-weighted fallback mid-price from current orderbook data
        fallback_mid = None
        if self.book_mids:
            # Calculate a volume-weighted mid-price for fallback
            total_book_volume = sum(vol for _, vol in self.book_mids.values())
            if total_book_volume > 0:
                weighted_sum = sum(px * vol for px, vol in self.book_mids.values())
                fallback_mid = weighted_sum / total_book_volume
            else:
                # Fallback to simple average if no volume
                prices = [px for px, _ in self.book_mids.values()]
                if prices:
                    fallback_mid = statistics.mean(prices)

        price, meta = self.oracle.compute(fallback_mid=fallback_mid)
        return price, meta

    def update_parameters(self, window_sec=None, startup_window_sec=None, min_notional=None):
        """
        Update feed parameters dynamically.

        Args:
            window_sec: New time window for trades in seconds (optional)
            startup_window_sec: New startup window length in seconds (optional)
            min_notional: New minimum notional value for trades (optional)
        """
        self.oracle.update_parameters(window_sec, startup_window_sec, min_notional)
        logging.info("Updated %s OracleFeed parameters: window_sec=%s, startup_window_sec=%s, min_notional=%s",
                    self.exchange_name, self.oracle.window, self.oracle.startup_window, self.oracle.min_notional)

    async def _create_websocket_task(self):
        """
        Create the WebSocket task for this exchange.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement _create_websocket_task")
