##
## Bot that bids in CircuitDAO liquidation auctions
##
## The bot:
## 1) monitors protocol state for vaults in liquidation
## 3) bids in ongoing auctions
## 4) hedges on-chain exposure on CEX(s)
## 5) closes positions


import os
import asyncio
import argparse
import logging
from dotenv import load_dotenv
from pprint import pprint

#from chia.types.coin_spend import compute_additions
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_hash_for_synthetic_public_key
from chia.wallet.util.compute_additions import compute_additions
from chia_rs import SpendBundle #Coin, G2Element, CoinSpend

from circuit_cli.client import CircuitRPCClient

# NOTE: Comment out maxRetries parameter in WsClientFactory.py
#   in the python-okx package, so that this script will continue to try to
#   resubscribe to the OKX websocket trades feed indefinitely if the connection gets lost
#from okx_async.websocket.WsPrivate import WsPrivate
from okx_async.websocket.WsPrivateAsync import WsPrivateAsync
from okx_async.AsyncTrade import AsyncTradeAPI

from keeper_bots.okx_feed import OkxFeed
from keeper_bots.okx_order_book import OkxOrderBook
from keeper_bots.okx_orders import OkxOrders
from keeper_bots.okx_balances import OkxBalances
from keeper_bots.utils import SPOT

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    filename="liquidation_bot.log",
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE, override=True)

rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
fee_per_cost = int(os.getenv("FEE_PER_COST")) # Fee per cost for transactions
CONTINUE_DELAY = int(os.getenv("LIQUIDATION_CONTINUE_DELAY")) # Wait (in seconds) before job runs again after a failed run
RUN_INTERVAL = int(os.getenv("LIQUIDATION_RUN_INTERVAL")) # Wait (in seconds) before job runs again after a failed interest withdrawal due to insufficiently large treasury coins
LIQUIDATION_MAX_BID_AMOUNT = int(os.getenv("LIQUIDATION_MAX_BID_AMOUNT"))
LIQUIDATION_MIN_MARGIN = float(os.getenv("LIQUIDATION_MIN_MARGIN"))


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


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

class TradeEnv(Enum):
    LIVE = "0"
    DEMO = "1"

async def test_order_placement_okx(collateral, proxy):

    market_okx, sym_okx = get_market(collateral, proxy)

    # For test purposes, wait a bit, then place and cancel an order
    await asyncio.sleep(10)

    flag = TradeEnv.DEMO # "0" = live, "1" = demo
    if flag == TradeEnv.DEMO:
        key = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_PASSPHRASE")
    elif flag == TradeEnv.LIVE:
        key = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_PASSPHRASE")

    tradeAPI = AsyncTradeAPI(key, secret, passphrase, flag=flag.value, debug=False)

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

    parser = argparse.ArgumentParser(description="Liquidation bot for Circuit protocol")
    parser.add_argument("-v", "--verbose", action='store_true', help="Verbose output")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="INFO")
    args = parser.parse_args()

    # Set log level dynamically
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    collateral_sym = "XCH" # CircuitDAO collateral asset
    stable = "BYC" # CircuitDAO stablecoin (BYC)

    # BYC proxy asset to be used for hedging
    # Note: on OKX, instID uniquely identifies the market. No need to know the instrument type.
    proxy_instrument = SPOT
    proxy = "USDT"

    # Market and OKX symbol corresponding to base and quote
    market, sym = get_market(collateral_sym, proxy)

    #sym = "XCH-USDT"
    uquote = "USD" # ultimate quote currency

    # Set trade environment
    flag = TradeEnv.DEMO
    if flag == TradeEnv.DEMO:
        okx_websocket_url = "wss://wseeapap.okx.com:8443/ws/v5/public"
        key = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_PASSPHRASE")
    elif flag == TradeEnv.LIVE:
        okx_websocket_url = "wss://wseea.okx.com:8443/ws/v5/public" # (wss://ws.okx.com:8443/ws/v5/public)
        key = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_PASSPHRASE")

    # Subscribe to order book
    okx_order_book = OkxOrderBook(market, uquote, okx_websocket_url, verbose=args.verbose)
    await okx_order_book.connect()
    await okx_order_book.subscribe()

    # Instantiate trade API
    tradeAPI = AsyncTradeAPI(key, secret, passphrase, flag=flag.value, debug=False)

    log.info("Running liquidation bot")

    margin = 0.25
    limit_order_buffer = 0.5 # this should be quite generous in case the market has moved
    base_amount_decimals = 4 # TODO: can we get this from OKX API in case it ever changes?
    price_decimals = 4 # TODO: can we get this from OKX API in case it ever changes?

    while True:

        #my_puzzle_hash = puzzle_hash_for_synthetic_public_key(rpc_client.synthetic_public_keys[0])
        balances = await rpc_client.wallet_balances()
        print("Wallet balances", balances)
        state = await rpc_client.upkeep_state(vaults=True)
        print("Protocol state", state)
        vaults_pending = state.get("vaults_pending_liquidation", [])
        print("%s vaults pending liquidation", len(vaults_pending))
        for vault in vaults_pending[:1]:
            try:
                response = await rpc_client.upkeep_vaults_liquidate(vault["name"])
            except httpx.ReadTimeout as err:
                log.error("Failed to get liquidation auction bid info due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to get liquidation auction bid info due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to liquidation auction bid info: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            print("Liquidation auction started for vault", vault["name"])
            print()

        debt = 50000
        auction_price = 8.7

        try:
            bid_info = rpc_client.upkeep_vaults_bid(vault["name"], debt, info=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get liquidation auction bid info due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get liquidation auction bid info due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to liquidation auction bid info: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        expected_collateral = bid_info["collateral_to_receive"]

        hedge_price, hedge_amount = okx_order_book.price("sell", expected_collateral, True)
        log.info(f"Can sell {expected_collateral} XCH at {hedge_price} {collateral_sym}/{proxy} for {hedge_amount} {proxy}")

        if hedge_amount > debt * (1 + margin):
            # bid for collateral
            try:
                response = await rpc_client.upkeep_vault_bid(vault["name"], debt)
            except httpx.ReadTimeout as err:
                log.error("Failed to place liquidation auction bid due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to place liquidation auction bid due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to place liquidation auction bid: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            if not response["success"]:
                log.error("Failed to place liquidation auction bid on OK. Response code: %s", response["success"])
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            # hedge on OKX
            while True:
                try:
                    response = await tradeAPI.place_order(
                        market, 'cash', 'sell', 'limit',
                        "{:.{}f}".format(hedge_amount, base_decimals),
                        px="{:.{}f}".format(hedge_price * limit_order_buffer, price_decimals),
                    )
                except Exception as err:
                    log.error("Failed to place order on OKX: %s", str(err))
                    await asyncio.sleep(1)
                    continue

                data = response["data"]

                if not data["code"] == 0:
                    # OKX failed to place order
                    log.error("Failed to place order on OKX. Response code: %s. Message: %s", data["code"], data["msg"])
                    await asyncio.sleep(1)
                    continue

                log.info("Successfully placed order: %s", json.dumps(data))
                break

            # reconcile balances
            #TODO:

            # recycle capital
            #TODO: USDT -> USDC --transfer to Base-> USDC.b --warp.green-> wUSDC.b -> BYC


        await asyncio.sleep(RUN_INTERVAL)


def main():
    asyncio.run(run_liquidation_bot())

if __name__ == '__main__':
    main()









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
