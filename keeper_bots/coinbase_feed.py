import os
import json
#import asyncio
#import aiofiles
#from collections import deque
#import math
from dotenv import load_dotenv

from coinbase.websocket import WSClient

load_dotenv()

API_KEY = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")


# Coinbase price feed
class CoinbaseFeed:

    def on_message(self, msg):
        d = json.loads(msg)
        if "channel" in d.keys():
            if d["channel"] == "ticker" and d["sequence_num"] > self.seq_num:
                self.seq_num = int(d["sequence_num"])
                self.price = float(d["events"][-1]["tickers"][-1]["price"])

    def __init__(self, quote, uquote):

        self.sym = f"{quote}-{uquote}"
        self.seq_num = -1 # First sequence number in Coinbase feed is 0
        self.price = None

        if quote != uquote:
            self.client = WSClient(api_key=API_KEY, api_secret=API_SECRET, on_message=self.on_message)
            self.client.open()
            self.client.subscribe(product_ids=[self.sym], channels=["ticker", "heartbeat"])
            print(f"Subscribed to Coinbase ticker for {self.sym}")
        else:
            self.client = None

