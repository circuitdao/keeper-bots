import os
import asyncio
import aiofiles
from collections import deque
import math
import json
import yaml
import logging.config
from datetime import datetime, timedelta

from okx_async.websocket.WsPublicAsync import WsPublicAsync

from keeper_bots.coinbase_feed import CoinbaseFeed

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("okx_feed")
#log = logging.getLogger(__name__)

# OKX price feed
# Keeps track of the volume-weighted average price based on trades on OKX
class OkxFeed(WsPublicAsync):

    def __init__(self, sym, uquote, url,
            startup_window_length=900,
            window_length=3600,
            verbose=False
    ):
        #print(f"Setting url on super class: {url}")
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

    async def __aenter__(self):
        log.info("Entering OkxFeed context, connecting to OKX websocket and subscribing...")
        await self.start()  # Connect to WebSocket
        await self.subscribe()  # Subscribe to trades channel
        return self  # Return the OkxFeed instance for use in 'async with'

    async def __aexit__(self, exc_type, exc_value, traceback):
        log.info("Exiting OkxFeed context, unsubscribing and closing OKX websocket connection...")
        try:
            await self.unsubscribe()  # Unsubscribe from trades channel
        except Exception as e:
            log.error("Error during unsubscribe: %s", str(e), extra={"exception": str(e)})
        finally:
            await self.stop()  # Close WebSocket connection

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
        #print("publicCallback", msg)
        message = json.loads(msg)
        if "event" in message:
            if message["event"] == "subscribe":
                # Initialise feed data
                self.starttime = datetime.utcnow()
                log.info("Subscribed to %s spot trades on OKX", self.sym)
                log.info("  Price calculation window length")
                try:
                    log.info("    on start-up: %ss", int(self.startup_window_length.total_seconds()))
                except Exception as err:
                    print(str(err))
                log.info("    post ramp-up: %ss", int(self.window_length.total_seconds()))
                log.info("  Start time (UTC): %s", self.starttime.strftime("%Y-%m-%d %H:%M:%S"))
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
                    if self.verbose: log.info("Popped: %s", old_trade)

                    if not self.feed:
                        break

                # Print new price
                if num_popped > 0:
                    log.info("New price: %.4f   (volume: %s)", self.price, sum([d[1] for d in self.feed]))

            # Add new trades
            for i in range(len(message["data"])):

                # Calculate price vs ultimate quote currency
                okx_price = float(message["data"][i]["px"])
                if self.bq()[1] != self.uquote:
                    price = okx_price * self.coinbase_feed.price
                else:
                    price = okx_price

                if self.verbose:
                    log.info("Prices:")
                    log.info("  %s %s (OKX)", okx_price, self.sym)
                    if self.coinbase_feed.client is not None:
                        log.info("  %s %s (Coinbase)", self.coinbase_feed.price, self.coinbase_feed.sym)
                        log.info("  %s %s-%s (OKX & Coinbase)", price, self.bq()[0], self.uquote)
                    log.info("")

                new_trade = [price, float(message["data"][i]["sz"]), datetime.utcfromtimestamp(int(message["data"][i]["ts"][:-3]))]
                self.recalculate_on_append(new_trade)

                # Append price and size from trade to feed
                self.feed.append(new_trade)
                if self.verbose: log.info("Appended: %s", new_trade)

            # Print new price
            if datetime.utcnow() - self.startup_window_length > self.starttime:
                if datetime.utcnow() - self.window_length < self.starttime:
                    ramp_up = " [ramp-up]"
                else:
                    ramp_up = ""
                log.info("New price%s: %.4f   (volume: %s)", ramp_up, self.price, sum([d[1] for d in self.feed]))
            else:
                if self.verbose: log.info("  No price yet. Still in start-up window")

        else:
            log.info("Dropping trade(s) as feed data not initialized yet")
            log.info("  %s", message)

    async def get_price(self):
        log.info("OKX price: %.4f", self.price)
        return self.price

    # Save price to text file
    async def save_price(self, save_frequency):
        now = datetime.utcnow()
        if now > self.starttime + self.window_length and self.starttime > datetime.utcfromtimestamp(0):
            if self.verbose: log.info("Writing price to file")
            async with aiofiles.open("okx_price.txt", "w") as f:
                await f.write(f'{now.strftime("%Y-%m-%d %H:%M:%S")}: \
                {str(self.price)} {self.sym} \
                (volume [{base}]: {self.size})')
