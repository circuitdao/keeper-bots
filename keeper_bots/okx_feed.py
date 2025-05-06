import asyncio
import aiofiles
from collections import deque
import math
import json
from datetime import datetime, timedelta

from okx_async.websocket.WsPublicAsync import WsPublicAsync

from coinbase_feed import CoinbaseFeed

# OKX price feed
# Keeps track of the volume-weighted average price based on trades on OKX
class OkxFeed(WsPublicAsync):

    def __init__(self, sym, uquote, url,
            startup_window_length=900,
            window_length=3600,
            verbose=False
    ):
        print(f"Setting url on super class: {url}")
        super().__init__(url)
        self.sym = sym
        assert len(self.bq()) == 2, "Symbol not valid. Must be of form <base>-<quote>"
        self.uquote = uquote
        self.startup_window_length = timedelta(seconds=startup_window_length)
        self.window_length = timedelta(seconds=window_length)
        self.starttime = datetime.utcfromtimestamp(0) # Begin of Unix epoch
        self.feed = deque() # List of [price, size] pairs
        self.price = float("NaN")
        self.size = 0
        #self.ws = None
        self.verbose = verbose
        self.coinbase_feed = CoinbaseFeed(self.bq()[1], uquote)

    # Wrap base class subscribe function
    async def subscribe(self):
        await super().subscribe([{"channel": "trades", "instId": self.sym}], self.__call__)

    # Wrap base class unsubscribe function
    async def unsubscribe(self):
        await super().unsubscribe([{"channel": "trades", "instId": self.sym}], self.__call__)

    def bq(self):
        return self.sym.split("-")

    def recalculate_on_append(self, append_trade):
        if math.isnan(self.price):
            self.price = append_trade[0]
            self.size = append_trade[1]
        else:
            new_size = self.size + append_trade[1]
            new_price = (self.price * self.size + append_trade[0] * append_trade[1])/new_size
            self.price = new_price
            self.size = new_size

    def recalculate_on_pop(self):
        if len(self.feed) == 1:
            self.price = float("NaN")
            self.size = 0
        elif len(self.feed) == 2:
            self.price = self.feed[1][0]
            self.size = self.feed[1][1]
        elif len(self.feed) > 2:
            pop_trade = self.feed[0]
            new_size = self.size - pop_trade[1]
            new_price = (self.price * self.size - pop_trade[0] * pop_trade[1])/new_size
            self.price = new_price
            self.size = new_size
        else:
            raise ValueError("Tried to call 'recalculate_on_pop' on empty feed")

    # Websocket callback function
    # Updates the price whenever a new trade is received
    def __call__(self, msg):
        print("publicCallback", msg)
        message = json.loads(msg)
        if "event" in message:
            if message["event"] == "subscribe":
                # Initialise feed data
                self.starttime = datetime.utcnow()
                print(f"Subscribed to {self.sym} spot trades on OKX")
                print(f"  Price calculation window length")
                print(f"    on start-up: {int(self.startup_window_length.total_seconds())}s")
                print(f"    post ramp-up: {int(self.window_length.total_seconds())}s")
                print(f'  Start time (UTC): {self.starttime.strftime("%Y-%m-%d %H:%M:%S")}')
            else:
                raise ValueError(f'Unknown event {message["event"]} returned in callback')
        elif "error" in message:
            raise Exception(f'Callback returned an error: {message["error"]}')
        elif self.starttime > datetime.utcfromtimestamp(0):

            # Drop old trades (if any) outside the price calculation window
            if self.feed:
                now = datetime.utcnow()
                num_popped = 0
                while self.feed[0][2] < now - self.window_length:

                    # Recalculate price
                    self.recalculate_on_pop()

                    old_trade = self.feed.popleft()
                    num_popped += 1
                    if self.verbose: print(f"Popped: {old_trade}")

                    if not self.feed:
                        break

                # Print new price
                if num_popped > 0:
                    print(f"  New price: {self.price}   (volume: {sum([d[1] for d in self.feed])})")

            # Add new trades
            for i in range(len(message["data"])):

                # Calculate price vs ultimate quote currency
                okx_price = float(message["data"][i]["px"])
                if self.bq()[1] != self.uquote:
                    price = okx_price * self.coinbase_feed.price
                else:
                    price = okx_price

                if self.verbose:
                    print("Prices:")
                    print(f"  {okx_price} {self.sym} (OKX)")
                    if self.coinbase_feed.client is not None:
                        print(f"  {self.coinbase_feed.price} {self.coinbase_feed.sym} (Coinbase)")
                        print(f"  {price} {self.bq()[0]}-{self.uquote} (OKX & Coinbase)")
                    print()

                new_trade = [price, float(message["data"][i]["sz"]), datetime.utcfromtimestamp(int(message["data"][i]["ts"][:-3]))]
                self.recalculate_on_append(new_trade)

                # Append price and size from trade to feed
                self.feed.append(new_trade)
                if self.verbose: print(f"Appended: {new_trade}")

            # Print new price
            if datetime.utcnow() - self.startup_window_length > self.starttime:
                if datetime.utcnow() - self.window_length < self.starttime:
                    ramp_up = " [ramp-up]"
                else:
                    ramp_up = ""
                print(f"  New price{ramp_up}: {self.price}   (volume: {sum([d[1] for d in self.feed])})")
            else:
                if self.verbose: print(f"  No price yet. Still in start-up window")

        else:
            print("WARNING: Dropping trade(s) as we haven't initialised our feed data yet")
            print(f"  {message}")

    async def get_price(self):
        print(f"PRICE: {self.price}")
        return self.price

    # Save price to text file
    async def save_price(self, save_frequency):
        now = datetime.utcnow()
        if now > self.starttime + self.window_length and self.starttime > datetime.utcfromtimestamp(0):
            if self.verbose: print("Writing price to file")
            async with aiofiles.open("okx_price.txt", "w") as f:
                await f.write(f'{now.strftime("%Y-%m-%d %H:%M:%S")}: \
                {str(self.price)} {self.sym} \
                (volume [{base}]: {self.size})')





## Connect to websocket
#async def connect(self):
#    #self.ws = WsPublic(url="wss://ws.okx.com:8443/ws/v5/public")
#    self.ws = WsPublicAsync(url="wss://ws.okx.com:8443/ws/v5/public")
#    #self.ws = WsPublic(url="wss://wspap.okx.com:8443/ws/v5/public")
#    #self.ws.start()
#    await self.ws.start()
#    print("Connected")
#
## Subscribe to websocket channel
#async def subscribe(self):
#    #self.ws.subscribe([{"channel": "trades", "instId": self.sym}], self)
#    #await self.ws.subscribe([{"channel": "trades", "instId": self.sym}], self)
#    #{"channel": "tickers", "instId": "ETH-USDT"}
#    await self.ws.subscribe([{"channel": "tickers", "instId": "ETH-USDT"}], publicCallback) #self)
#    print("Subscribed")
