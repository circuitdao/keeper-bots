import os
import asyncio
from pprint import pprint
from okx_async.AsyncTrade import AsyncTradeAPI
from utils import parse_symbol


class OkxOrders:
    def __init__(self, instrument, market, verbose=False):
        self.instrument = instrument
        self.market = market
        self.verbose = verbose
        self.orders = {} # Orders by order ID. Note: these are always limit orders as we don't place anything else
        self.subscribed = False # Set to true once we are subscribed to the order channel
        self.snapshot_taken = False # Set to true once snapshot of orders has been taken

    def parse_symbol(self, symbol_name, symbol=None):
        return parse_symbol("OKX", symbol_name, symbol)

    def parse_order(self, order):

        return {
            "exchange": "OKX",
            "orderId": order["ordId"],
            "timestamp": self.parse_symbol("uTime", order["uTime"]), # Updated at [in Unix time in milliseconds]
            "instrument": self.parse_symbol("instType", order["instType"]), # E.g. 'SPOT'
            "market": self.parse_symbol("instId", order["instId"]), # E.g. 'XCH-USDT'
            "orderType": self.parse_symbol("ordType", order["ordType"]), # 'limit', 'market', etc.
            "state": self.parse_symbol("state", order["state"]), # 'canceled', 'live', 'partially_filled', 'filled', 'mmp_canceled'
            "side": self.parse_symbol("side", order["side"]), # 'buy' or 'sell'
            "lastFilled": order["fillSz"], # Size of last fill
            "totalFilled": order["accFillSz"], # Size filled
            "size": order["sz"], # Order size
            "avgFillPrice": order["avgPx"], # Is 0 if totalFilled is 0
            "price": order["px"] # Order price
        }

    def print_orders(self):
        print("Orders:")
        pprint(self.orders)

    async def take_snapshot(self):
        print("TAKING SNAPSHOT OF ORDERS")

        flag = "0" # "0" = live, "1" = demo
        tradeAPI = AsyncTradeAPI(os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_API_PASSPHRASE"), flag=flag, debug=False)

        response = await tradeAPI.get_order_list()
        snapshot = response["data"]
        #pprint(snapshot)

        for o in snapshot:
            orderId = o["ordId"]
            # If order does not exist in self.orders and matches instrument and market specified, add it
            if not orderId in self.orders.keys() and self.instrument == o["instType"] and self.market == o["instId"]:
                self.orders[orderId] = self.parse_order(o)
            else: # Order does exist in self.orders or is an instrument or on a market we are not interested in
                # If snapshot uTime <= self.orders uTime, then discard snapshot order, else (which should never happen) replace order in self.orders
                pass

        # Stop adding filled and cancelled orders to self.orders
        self.snapshot_taken = True
        await asyncio.sleep(5) # We wait for a few seconds, just in case there is latency on the order stream and cancellations are delayed. But even if there's cancelled orders left in self.orders, that's fine and not an actual issue.

        # Remove cancelled orders from self.orders
        for k, v in self.orders.items():
            if v["state"] in ["filled", "canceled", "mmp_canceled"]:
                self.orders.pop(k)

        # Print orders
        if self.verbose: self.print_orders()

    def __call__(self, message):
        #pprint(message)
        if "event" in message:
            if message["event"] == "subscribe":
                self.subscribed = True
                print(f'Subscribed to channel {message["arg"]["channel"]}')
            else:
                print(f'WARNING: Unrecognised event {message["event"]}')
        elif "data" in message:
            for d in message["data"]:
                orderId = d["ordId"]
                if d["state"] in ["filled", "canceled", "mmp_canceled"]:
                    print(f'Order (ID {orderId}) was {d["state"]}')
                    if not self.snapshot_taken:
                        # If snapshot has not been taken yet, we store filled and cancelled orders as these may have to be deleted from snapshot
                        self.orders[orderId] = self.parse_order(d)
                    else:
                        # If snapshot has been taken, we delete filled and cancelled orders
                        if orderId in self.orders.keys():
                            self.orders.pop(orderId)
                        else:
                            print(f'WARNING: Order with ID {orderId} and state {d["state"]} seems to have already been removed from Orders instance')
                elif d["state"] in ["live", "partially_filled"]:
                    print(f'Order (ID {orderId}) is {d["state"]}')
                    if orderId in self.orders:
                        if d["uTime"] > self.orders[orderId]["timestamp"]:
                            self.orders[orderId] = self.parse_order(d)
                        else:
                            # This might happen due to async processing. Small probability/rare, tough.
                            print(f'WARNING: Stale order detected in order feed:')
                            pprint(d)
                    else:
                        self.orders[orderId] = self.parse_order(d)
                else:
                    print(f'WARNING: Unknown order state {d["state"]} of order with ID {orderId}')
        else:
            print(f'WARNING: Do not know how to handle message without "data" field from "order" channel:')

        if self.verbose: self.print_orders()
