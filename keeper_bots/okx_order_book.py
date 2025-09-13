import os
import json
from dotenv import load_dotenv
from typing import Optional
from utils import unparse_symbol
from datetime import datetime
#import okx_async.AsyncMarketData as AsyncMarketData
#from okx_async.websocket.WsPublic import WsPublic
from okx_async.websocket.WsPublicAsync import WsPublicAsync

from pprint import pprint


class OkxOrderBook:
    """OKX order book class

    Internally, the order book is a dict. Its fields include 'asks' and 'bids', each being a dict of price level to volume.
    """

    def __init__(self, sym, uquote, url, verbose=False):
        self.sym = sym
        assert len(self.bq()) == 2, "Symbol not valid. Must be of form <base>-<quote>"
        self.uquote = uquote
        #self.starttime = datetime.utcfromtimestamp(0) # Begin of Unix epoch
        self.ws = None
        self.connection_id = None
        self.verbose = verbose
        self.book = {}
        self.url = url
        #self.coinbase_feed = CoinbaseFeed(self.bq()[1], uquote)

    def bq(self):
        return self.sym.split("-")

    # Connect to websocket
    async def connect(self):
        print(f"Connecting to OKX websocket at {self.url}")
        ## Production: wss://wseea.okx.com:8443/ws/v5/public (wss://ws.okx.com:8443/ws/v5/public)
        ## Demo: wss://wseeapap.okx.com:8443/ws/v5/public
        self.ws = WsPublicAsync(url=self.url)
        await self.ws.start()

    # Subscribe to 'books' websocket channel
    async def subscribe(self):
        print("Subscribing to OKX order book")
        await self.ws.subscribe([{"channel": "books", "instId": self.sym}], self)
        print("Subscribed to OKX order book")

    def print(self):
        print("Order book:")
        pprint(self.book)

    # Update order book
    """Info at: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-order-book-channel"""
    def __call__(self, message):
        #print("publicCallback", message)
        message = json.loads(message)
        if "event" in message:
            if message["event"] == "subscribe":
                # Initialise feed data
                self.starttime = datetime.utcnow()
                self.connection_id = message["connId"]
                print(f"Subscribed to {self.sym} order book on OKX")
                print(f"  Connection ID: {self.connection_id}")
                print(f'  Start time (UTC): {self.starttime.strftime("%Y-%m-%d %H:%M:%S")}')
            else:
                raise ValueError(f'Unknown event {message["event"]} returned in callback')
        elif "error" in message:
            raise Exception(f'Callback returned an error: {message["error"]}')
        elif "action" in message:
            if message["action"] == "snapshot":
                print("ORDER BOOK SNAPSHOT received")
                if len(message["data"]) > 1:
                    print("WARNING: More than one order book snapshot received")
                for d in message["data"]:
                    for side in ["asks", "bids"]:
                        self.book[side] = {}
                        for depth in d[side]:
                            self.book[side][depth[0]] = depth[1]
            elif message["action"] == "update":
                print("ORDER BOOK UPDATE received")
                if len(message["data"]) > 1:
                    print("WARNING: More than one order book update received")
                for d in message["data"]:
                    for side in ["asks", "bids"]:
                        for depth in d[side]:
                            if float(depth[1]) > 0:
                                self.book[side][depth[0]] = depth[1]
                            else:
                                self.book[side].pop(depth[0])
                        ## Order side of order book
                        #reverse = True if side == "bids" else False
                        #self.book[side] = dict(sorted(self.book[side].items(), key=dicttofloat, reverse=reverse)[:DEPTH])
                    assert len(self.book["asks"]) == 400, "OKX order book ask depth not equal to 400" # TODO: handle gracefully
                    assert len(self.book["bids"]) == 400, "OKX order book bid depth not equal to 400" # TODO: handle gracefully
            else:
                raise Exception(f'Unknown action {message["action"]} returned in callback')

    def mid_price(self) -> float:
        lowest_ask = min([float(key) for key in self.book["asks"].keys()])
        highest_bid = max([float(key) for key in self.book["bids"].keys()])
        return (lowest_ask + highest_bid) / 2

    def price(self, direction: str, amount: int, bq_toggle: bool) -> tuple[float, float, float]:
        """Price at which an amount of currency can be bought or sold

        Returns average price and max/min price at which amount can be bought or sold and corresponding amount.

        If order book isn't deep enough to cover requested amount, prices and amount returned reflect
        all liquidity in order book being used up.

        Note: size and amount values are in currency given by bq_toggle. volume is always in base currency.

        Arguments:
        direction - "buy" or "sell"
        amount - amount to buy or sell
        bq_toggle - whether amount is measured in base (True) or quote (False) currency
        """
        if not self.book:
            return None, None

        def dicttofloat(data):
            return float(data[0])

        def volume_to_size(volume, price, bq_toggle):
            """Return amount of base or quote currency (depending on bq_toggle) equivalent to given base currency volume."""
            return volume if bq_toggle else price * volume

        def size_to_volume(size, price, bq_toggle):
            """Return amount of base currency equivalent to given base or quote currency (depending on bq_toggle) size."""
            return size if bq_toggle else size / price

        size = 0 # In same currency as amount (as given by bq_toggle)
        price = None
        if direction == "buy":
            side = "asks"
        elif direction == "sell":
            side = "bids"
        else:
            raise Exception(f"Unkonwn direction '{direction}'")

        reverse = True if side == "bids" else False
        self.book[side] = dict(sorted(self.book[side].items(), key=dicttofloat, reverse=reverse))

        cnt = 0
        #prev_d: int
        last_price = None
        for d, v in self.book[side].items():

            """ Check that order book side is ordered (to be deleted)
            if cnt >= 1:
                if side == "bids":
                    assert prev_d > d
                else:
                    assert prev_d < d
            prev_d = d
            cnt += 1
            """

            #cnt += 1
            level = float(d)
            volume = min(float(v), size_to_volume(amount - size, level, bq_toggle)) # NOTE: volume is always base currency

            #print(f"Level no. {cnt}")
            #print(f"  Average price so far:  {price}")
            #print(f"  Size so far:   {size}/{amount}")
            #print(f"  Level price:  {level}")
            #print(f"  Level volume taken: {volume}")

            if price is None:
                price = level
                size = volume_to_size(volume, level, bq_toggle)
            else:
                price = (price * size_to_volume(size, price, bq_toggle) + level * volume) / (size_to_volume(size, price, bq_toggle) + volume)
                size += volume_to_size(volume, level, bq_toggle)
            #if volume < float(v):
            #    break
            if size >= amount:
                break

        return price, level, size


"""
class OrderBook:
    def __init__(self, instrument, market, verbose=False):
        self.instrument = instrument
        self.market = market
        self.verbose = verbose
        self.url = "https://aws.okx.com/api/v5/market/books" # URL from which order book is retrieved
        self.params = { # Parameters used to specify what order book info to retrieve
            "instId": unparse_symbol("OKX", self.instrument, self.market)[1],
            "sz": 400 # 400 is max depth
        }
        self.order_book = {} # Order book for the given market. Dict with keys 'asks', 'bids' and 'ts'. Asks and bids are each a list of lists [price level, total volume at price level, "0", number of orders at price level]

    def print_order_book(self):
        print("Order book:")
        pprint(self.order_book)

    # Update order book
    async def __call__(self):

        flag = "0" # "0" = live, "1" = demo
        marketDataAPI = AsyncMarketData.AsyncMarketAPI(os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_API_PASSPHRASE"), flag=flag, debug=False)

        self.order_book = await marketDataAPI.get_orderbook(self.params["instId"], self.params["sz"])
        if self.verbose: self.print_order_book()
"""
