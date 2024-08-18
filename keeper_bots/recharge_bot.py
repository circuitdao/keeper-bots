##
## Bot that participates in CircuitDAO recharge auctions
##
## The bot:
## 1) monitors treasury
## 2) triggers recharge auction
## 3) bids BYC for CRT
## if won:
## 4) executes win operation
## 5) sells off CRT for BYC via offer files
##

import os
import asyncio
from dotenv import load_dotenv
from pprint import pprint

# NOTE: Comment out maxRetries parameter in WsClientFactory.py
#   in the python-okx package, so that this script will continue to try to
#   resubscribe to the OKX websocket trades feed indefinitely if the connection gets lost
from okx_async.websocket.WsPrivate import WsPrivate
from okx_async.AsyncTrade import AsyncTradeAPI

#from cdv.cmds.rpc import rpc_state_cmd as blockchain_state

from orders import Orders
from orderBook import OrderBook
from balances import Balances
from utils import SPOT


def get_market(base, quote):
    """Return internal symbols for market and price"""

    return [f"{base}-{quote}", f"{base}/{quote"]


async def listen_to_balances_okx(ws_okx_private, balances_okx, verbose=False):

    # Subscribe to account balances
    args_balances_okx = []
    for a in balances_okx.balances.keys():
        args_balances_okx.append({"channel": "account", "ccy": a, "extraParams": "{\"updateInterval\": 0}"})

    ws_okx_private.subscribe(args_balances_okx, callback=balances_okx)


async def listen_to_order_book_okx(order_book_okx, verbose=False):
    frequency = 100 # Frequency (in seconds) with which to retrieve order book
    while True:
        await order_book_okx.__call__()
        if verbose:
            print("PRINTING ORDER BOOK FROM listen_to_order_book_okx")
            pprint(order_book_okx.order_book)
        await asyncio.sleep(frequency)


async def listen_to_orders_okx(ws_okx_private, orders_okx, verbose=False):

    # Subscribe to orders
    args_orders_okx = []
    args_orders_okx.append({"channel": "orders", "instType": "SPOT", "instId": orders_okx.market})

    ws_okx_private.subscribe(args_orders_okx, callback=orders_okx)

    # Since the channel doesn't provide a snapshot, we have to GET one ourselves!
    # First wait until we are subscribed, so that we don't miss any updates after the snapshot
    while not orders_okx.subscribed:
        await asyncio.sleep(1)

    # Take the snapshot
    await orders_okx.take_snapshot()


# Listen to all activity within the CircuitDAO protocol
async def listen_to_protocol(collateral, stable, proxy):

    await asyncio.sleep(1)


async def test_order_placement_okx(collateral, proxy):

    market_okx, sym_okx = get_market(collateral, proxy)

    # For test purposes, wait a bit, then place and cancel an order
    await asyncio.sleep(10)

    flag = "0" # "0" = live, "1" = demo
    tradeAPI = AsyncTradeAPI(os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_API_PASSPHRASE"), flag=flag, debug=False)

    response = (await tradeAPI.place_order(market, 'cash', 'buy', 'limit', '0.17', px='15.8'))["data"]
    print("Order placed:")
    pprint(response)

    await asyncio.sleep(1)

    response = (await tradeAPI.cancel_order(market_okx, response[0]["ordId"]))["data"]
    print("Order cancelled:")
    pprint(response)


async def test_order_placement_onchain(collateral, stable):

    market_onchain, sym_onchain = get_market(collateral, stable)

    # For test purposes, wait a bit, then place and cancel an order
    await asyncio.sleep(10)


async def run_recharge_bot(collateral, stable, proxy_instrument, proxy, verbose=False):

    load_dotenv()

    # Market and OKX symbol corresponding to base and quote
    market, sym = get_market(collateral, proxy)

    # Instantiate OKX balances object
    balances_okx = Balances(assets=[collateral, proxy], verbose=verbose)

    # Instantiate OKX order book object
    order_book_okx = OrderBook(proxy_instrument, market, verbose=False)

    # Instantiate OKX orders object
    orders_okx = Orders(proxy_instrument, market, verbose=verbose)

    # Connect to private OKX websocket
    ws_okx_private = WsPrivate(apiKey=os.getenv("OKX_API_KEY"),
                               passphrase=os.getenv("OKX_API_PASSPHRASE"),
                               secretKey=os.getenv("OKX_API_SECRET"),
                               url="wss://wsaws.okx.com:8443/ws/v5/private",
                               useServerTime=False)
    ws_okx_private.start()

    # Listen to market and blockchain and participate in recharge auctions
    await asyncio.gather(listen_to_balances_okx(ws_okx_private, balances_okx, verbose),
                         listen_to_order_book_okx(order_book_okx, verbose=False),
                         listen_to_orders_okx(ws_okx_private, orders_okx, verbose),
                         listen_to_treasury(collateral, stable, proxy), # TODO
                         test_order_placement_okx(collateral, proxy),
                         test_order_placement_onchain(collateral, stable))

    # Instantiante on-chain balances object
    #balances_onchain = Balances(assets=[collateral, stable], verbose=verbose)


if __name__ == '__main__':

    # Set output verbosity
    verbose = True # True or False

    collateral = "XCH" # CircuitDAO collateral asset
    stable = "BYC" # CircuitDAO stablecoin (BYC)

    # BYC proxy asset to be used for hedging
    # Note: on OKX, instID uniquely identifies the market. No need to know the instrument type.
    proxy_instrument = SPOT
    proxy = "USDT"

    asyncio.run(run_recharge_bot(collateral, stable, proxy_instrument, proxy, verbose))

