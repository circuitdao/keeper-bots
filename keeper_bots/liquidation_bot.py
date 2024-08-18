##
## Liquidation bot that participates in CircuitDAO liquidation auctions
##
## The bot:
## 1) Monitors protocol
## 2) triggers liquidation auction
## 3) bids in auction
## 4) hedges on-chain exposure on CEX(s)
## 5) closes positions
##

import os
import asyncio
import argparse
from dotenv import load_dotenv
from pprint import pprint

from chia.types.coin_spend import compute_additions
from chia.types.spend_bundle import SpendBundle
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_hash_for_synthetic_public_key

from circuit_cli.client import CircuitRPCClient

# NOTE: Comment out maxRetries parameter in WsClientFactory.py
#   in the python-okx package, so that this script will continue to try to
#   resubscribe to the OKX websocket trades feed indefinitely if the connection gets lost
from okx_async.websocket.WsPrivate import WsPrivate
from okx_async.AsyncTrade import AsyncTradeAPI

from okx_feed import OkxFeed
from okx_order_book import OkxOrderBook
from okx_orders import OkxOrders
from okx_balances import OkxBalances
from utils import SPOT


def get_market(base, quote):
    """Return internal symbols for market and price"""

    return [f"{base}-{quote}", f"{base}/{quote}"]


async def listen_to_balances_okx(ws_okx_private, balances_okx, verbose=False):

    # Subscribe to account balances
    args_balances_okx = []
    for a in balances_okx.balances.keys():
        args_balances_okx.append({"channel": "account", "ccy": a, "extraParams": "{\"updateInterval\": 0}"})

    ws_okx_private.subscribe(args_balances_okx, callback=balances_okx)


async def listen_to_order_book_okx(order_book_okx, verbose=False):
    frequency = 10 # Frequency (in seconds) with which to retrieve order book
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

    response = (await tradeAPI.place_order(market_okx, 'cash', 'buy', 'limit', '0.17', px='5.7'))["data"]
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

# TODO: take fees into account (on- and off-chain)

async def run_liquidation_bot():

    load_dotenv()

    parser = argparse.ArgumentParser(description="Liquidation bot for Circuit protocol")
    parser.add_argument(
        "--base-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default="http://localhost:8000",
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument("--private_key", "-p", type=str, default=os.environ.get("PRIVATE_KEY"), help="Bot wallet master private key")
    parser.add_argument("-b", "--max-bid-amount", type=int, required=True, help="Max BYC bid size")
    parser.add_argument("-m", "--min-margin", type=float, required=True, help="Min margin between XCH market price and bid price")
    parser.add_argument("-v", "--verbose", action='store_true', help="Verbose output")

    args = parser.parse_args()

    rpc_client = CircuitRPCClient(args.base_url, args.private_key)

    collateral = "XCH" # CircuitDAO collateral asset
    stable = "BYC" # CircuitDAO stablecoin (BYC)

    # BYC proxy asset to be used for hedging
    # Note: on OKX, instID uniquely identifies the market. No need to know the instrument type.
    proxy_instrument = SPOT
    proxy = "USDT"

    # Market and OKX symbol corresponding to base and quote
    market, sym = get_market(collateral, proxy)

    sym = "XCH-USDT"
    uquote = "USD" # ultimate quote currency

    """
    # Connect to OKX price feed
    startup_window_length = 10
    window_length = 60

    if startup_window_length > window_length:
        raise ValueError("Start-up window must not be longer than full window")

    okx_feed = OkxFeed(sym, uquote, startup_window_length, window_length, verbose=args.verbose)
    okx_feed.connect()
    okx_feed.subscribe()
    """

    okx_order_book = OkxOrderBook(sym, uquote, verbose=args.verbose)
    okx_order_book.connect()
    okx_order_book.subscribe()

    while True:

        # Check for liquidatable vaults
        response = rpc_client.client.get("/protocol/state")
        if response.status_code != 200:
            print("Failed to get protocol state", response.content)
        state = response.json()
        my_puzzle_hash = puzzle_hash_for_synthetic_public_key(rpc_client.synthetic_public_keys[0])
        balances = await rpc_client.wallet_balances()
        print("Wallet balances", balances)
        print("Protocol state", state)
        if state["vaults_pending_liquidation"]:
            vaults_pending = state["vaults_pending_liquidation"]
            print("Vaults pending liquidation", vaults_pending)
            for vault in vaults_pending:
                vault_name = vault["name"]
                response = rpc_client.client.post(
                    "/vaults/start_auction",
                    json={
                        "synthetic_pks": [key.to_bytes().hex() for key in rpc_client.synthetic_public_keys],
                        "vault_name": vault_name,
                        "initiator_puzzle_hash": my_puzzle_hash.hex(),
                    }
                )
                if response.status_code != 200:
                    print("Failed to start liquidation auction for vault", vault_name)
                    print(response.content)
                    continue
                auction_bundle = SpendBundle.from_json_dict(response.json())
                signed_bundle = await rpc_client.sign_and_push(auction_bundle)
                print()
                print("Auction bundle:")
                #print(auction_bundle)
                for cs in auction_bundle.coin_spends:
                    additions = compute_additions(cs)
                    print("  Additions:")
                    for c in additions:
                        if c.amount > 1:
                            print(f"    {c.name().hex()}")
                print("Liquidation auction started for vault", vault_name)
                print()


        #okx_order_book.print()
        price, amount = okx_order_book.price("buy", 50000, False)
        print(f"Can buy {proxy} {amount} worth of XCH at {price} {collateral}/{proxy}")
        price, amount = okx_order_book.price("sell", 50000, False)
        print(f"Can sell {proxy} {amount} worth of XCH at {price} {collateral}/{proxy}")

        await asyncio.sleep(30)

    """
    # Instantiate OKX balances object
    balances_okx = Balances(assets=[collateral, proxy], verbose=args.verbose)

    # Instantiate OKX order book object
    order_book_okx = OrderBook(proxy_instrument, market, verbose=args.verbose)

    # Instantiate OKX orders object
    orders_okx = Orders(proxy_instrument, market, verbose=args.verbose)

    # Connect to private OKX websocket
    ws_okx_private = WsPrivate(apiKey=os.getenv("OKX_API_KEY"),
                               passphrase=os.getenv("OKX_API_PASSPHRASE"),
                               secretKey=os.getenv("OKX_API_SECRET"),
                               url="wss://wsaws.okx.com:8443/ws/v5/private",
                               useServerTime=False)
    ws_okx_private.start()

    # Listen to market and blockchain and participate in liquidation auctions
    await asyncio.gather(listen_to_balances_okx(ws_okx_private, balances_okx, args.verbose),
                         listen_to_order_book_okx(order_book_okx, verbose=args.verbose),
                         listen_to_orders_okx(ws_okx_private, orders_okx, args.verbose),
                         #listen_to_liquidations(collateral, stable, proxy, args.verbose),
                         test_order_placement_okx(collateral, proxy),
                         test_order_placement_onchain(collateral, stable))

    # Instantiante on-chain balances object
    #balances_onchain = Balances(assets=[collateral, stable], verbose=verbose)
    """

def main():
    asyncio.run(run_liquidation_bot())

if __name__ == '__main__':
    main()

