import copy
import math
from decimal import *

class OkxBalances:
    def __init__(self, assets, verbose=False):
        self.verbose = verbose
        self.balances = {} # List of assets whose balances we keep track of
        self.nan_dict = {
            "available": float("NaN"),
            "locked": float("NaN"),
            "total": float("NaN")
        }
        self.zero_dict = {
            "available": '0',
            "locked": '0',
            "total": '0'
        }
        for a in assets:
            self.balances[a] = copy.deepcopy(self.nan_dict)


    def print_balances(self, delta):
        print("Balances:")
        for a in self.balances.keys():
            print(f"  {a}")
            if a in delta.keys():
                print(f'    available: {self.balances[a]["available"]}   ---   delta: {delta[a]["available"]}')
                print(f'    locked: {self.balances[a]["locked"]}   ---   delta: {delta[a]["locked"]}')
                print(f'    total: {self.balances[a]["total"]}   ---   delta: {delta[a]["total"]}')
            else:
                print(f'    available: {self.balances[a]["available"]}')
                print(f'    locked: {self.balances[a]["locked"]}')
                print(f'    total: {self.balances[a]["total"]}')


    # Websocket callback function
    # Update balances
    def __call__(self, message):
        #pprint(message)
        if "event" in message:
            if message["event"] == "subscribe":
                print(f'Subscribed to channel {message["arg"]["channel"]} for asset {message["arg"]["ccy"]}')
            else:
                print(f'WARNING: Unrecognised event {message["event"]}')
        elif "data" in message:
            delta = {} # Delta to previous balances
            for d in message["data"]:
                if "details" in d:
                    for det in d["details"]:
                        asset = det["ccy"]
                        if asset in self.balances.keys():
                            if not True in [math.isnan(v) for v in [float(s) for s in self.balances[asset].values()]]:
                                delta[asset] = {
                                    "available": Decimal(det["availBal"]) - Decimal(self.balances[asset]["available"]),
                                    "total": Decimal(det["cashBal"]) - Decimal(self.balances[asset]["total"]),
                                    "locked": Decimal(det["frozenBal"]) - Decimal(self.balances[asset]["locked"])
                                }
                            else:
                                delta[asset] = copy.deepcopy(self.nan_dict)

                            # Assign new balances
                            self.balances[asset]["available"] = det["availBal"]
                            self.balances[asset]["total"] = det["cashBal"]
                            self.balances[asset]["locked"] = det["frozenBal"] # There's also an ordFrozen field. No idea what for. Equals frozenBal

                else:
                    print(f'WARNING: Do not know how to handle data without "details" field')

                # Print balances
                if self.verbose: self.print_balances(delta)

        else:
            print(f'WARNING: Cannot handle message without "event" and "data" fields')
