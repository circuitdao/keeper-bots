#import os
#import json
import asyncio
#import aiofiles
#from collections import deque
#import math
#from datetime import datetime, timedelta
#from dotenv import load_dotenv
#from pprint import pprint

#from coinbase.websocket import WSClient
#from okx_async.websocket.WsPublic import WsPublic

from keeper_bots.okx_feed import OkxFeed


# Subscribe to OKX websocket and listen to trades
async def listen_to_okx_trades(base, quote, uquote, startup_window_length, window_length, save_frequency, verbose=False):

    # Check that start-up window is not longer than full window
    if startup_window_length > window_length:
        raise ValueError("Start-up window must not be longer than full window")

    # Market and symbol corresponding to base and quote
    market = f"{base}-{quote}"
    sym = f"{base}/{quote}"

    # Instantiante feed object
    okx_feed = OkxFeed(sym, uquote, startup_window_length, window_length, verbose)

    okx_feed.connect()
    okx_feed.subscribe(market)

    # Connect to websocket
    #ws = WsPublic(url="wss://wsaws.okx.com:8443/ws/v5/public")
    #ws.start()

    # Subscribe to channel
    #ws.subscribe([{"channel": "trades", "instId": market}], feed)

    # Periodically save oracle price to file once we are post ramp-up
    while True:
        await okx_feed.save_oracle_price(save_frequency)
        await asyncio.sleep(save_frequency)


if __name__ == "__main__":

    # Set output verbosity
    verbose = True # True or False

    # Specify which OKX market we are getting base currency data from
    base = "XCH"
    quote = "USDT"

    # Specify which Coinbase market we can get quote currency data priced in ultimate quote currency from
    uquote = "USD" # ultimate quote currency (if uquote = quote, then only OKX data is subscribed to)

    # Trade feed from market relevant for Oracle price calculation
    # |- start-up window -|-> start publishing oracle price w/ ramp-up warning
    #                     |-          ramp-up window          -|-> start publishing oracle price w/o ramp-up warning
    # |-                    window                            -|-> full window starts moving, old feed data gets dropped
    #   |-                    window                            -|
    #     |-                    window                            -|
    startup_window_length = 30 # 15*60 Length of start-up window [in seconds]
    window_length = 60 # 60*60 Length of full window (start-up + ramp-up) [in seconds]
    save_frequency = 60 # How often (in seconds) we write the oracle price to file

    print("Starting up")

    # Listen to spot trades on OKX for seclected pair
    asyncio.run(listen_to_okx_trades(base, quote, uquote, startup_window_length, window_length, save_frequency, verbose=verbose))
