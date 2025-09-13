##
## Bot that bids in CircuitDAO liquidation auctions
##
## The bot:
## 1) monitors protocol state for vaults in liquidation
## 3) bids in ongoing liquidation auctions
## 4) hedges positions on OKX

import os
import asyncio
import httpx
import argparse
import logging
import yaml
import json
from enum import Enum
from math import ceil
from dotenv import load_dotenv

from circuit_cli.client import CircuitRPCClient

# NOTE: Comment out maxRetries parameter in WsClientFactory.py
#   in the python-okx package, so that this script will continue to try to
#   resubscribe to the OKX websocket trades feed indefinitely if the connection gets lost
from okx_async.websocket.WsPrivateAsync import WsPrivateAsync
from okx_async.AsyncTrade import AsyncTradeAPI
from okx_async.AsyncAccount import AsyncAccountAPI

from keeper_bots.okx_order_book import OkxOrderBook
from keeper_bots.utils import SPOT


PRICE_PRECISION = 100
MOJOS_PER_XCH = 10**12
MCAT=1000

class TradeEnv(Enum):
    LIVE = "0"
    DEMO = "1"

class BidFail(Enum):
    NONE = 0
    NOT_POSSIBLE = 1
    NOT_PROFITABLE = 2

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("liquidation_bid_bot")

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE, override=True)

rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
fee_per_cost = int(os.getenv("FEE_PER_COST")) # Fee per cost for transactions
CONTINUE_DELAY = int(os.getenv("LIQUIDATION_CONTINUE_DELAY")) # Wait (in seconds) before job runs again after a failed run
RUN_INTERVAL = int(os.getenv("LIQUIDATION_RUN_INTERVAL")) # Wait (in seconds) before job runs again after a failed interest withdrawal due to insufficiently large treasury coins
max_bid_amount = os.getenv("LIQUIDATION_MAX_BID_AMOUNT") # max bid amount (in mBYC). If None, there's no cap on bid amount
MAX_BID_AMOUNT = int(max_bid_amount) if max_bid_amount and max_bid_amount.strip() else None
MARGIN = float(os.getenv("LIQUIDATION_MARGIN"))


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


def get_okx_symbols(base_symbol, quote_symbol):
    """Return OKX symbols for market and price"""
    return f"{base_symbol}-{quote_symbol}", f"{base_symbol}/{quote_symbol}"

async def liquidate_vault(
        vault_name, rpc_client, okx_order_book, tradeAPI, accountAPI, base_decimals, price_decimals,
        collateral_symbol, stablecoin_symbol, proxy_symbol, market_symbol, price_symbol,
) -> BidFail:
    log.info("Liquidating vault %s", vault_name)
    # get vault collateral, debt and max byc amount to bid
    try:
        log.info("Getting bid info for vault %s", vault_name)
        bid_info = await rpc_client.upkeep_vaults_bid(vault_name, info=True)
    except httpx.HTTPStatusError as err:
        log.error("Failed to get liquidation auction bid info due to HTTPStatusError: %s", err)
        raise
    except httpx.ReadTimeout as err:
        log.error("Failed to get liquidation auction bid info due to ReadTimeout: %s", err)
        raise
    except ValueError as err:
        log.error("Failed to get liquidation auction bid info due to ValueError: %s", err)
        raise
    except Exception as err:
        log.error("Failed to get liquidation auction bid info: %s", err)
        raise

    if not bid_info["action_executable"]:
        log.info("Cannot place bid in liquidation auction for vault %s", vault_name) # LATER: provide additional info on why not
        return BidFail.NOT_POSSIBLE

    auction_price = bid_info["auction_price"]
    collateral = bid_info["collateral"]
    debt = bid_info["debt"]
    max_byc_amount_to_bid = bid_info["max_byc_amount_to_bid"]
    leftover_collateral = bid_info["leftover_collateral"]

    if debt > max_byc_amount_to_bid and not leftover_collateral == 0:
        # this should never happen! bid should use up all collateral if max amount to bid is less than debt
        log.warning(
            "There is leftover collateral (%s) despite max amount to bid being less than debt (%s < %s)",
            leftover_collateral, max_byc_amount_to_bid, debt
        )

    log.info(
        "Vault %s has collateral=%s %s  debt=%s m%s  max amount to bid=%s m%s",
        vault_name, collateral/MOJOS_PER_XCH, collateral_symbol, debt, stablecoin_symbol, max_byc_amount_to_bid, stablecoin_symbol
    )

    # get wallet byc balance
    try:
        balances = await rpc_client.wallet_balances()
    except httpx.HTTPStatusError as err:
        log.error("Failed to get wallet balances due to HTTPStatusError: %s", err)
        raise
    except httpx.ReadTimeout as err:
        log.error("Failed to get wallet balances due to ReadTimeout: %s", err)
        raise
    except ValueError as err:
        log.error("Failed to get wallet balances due to ValueError: %s", err)
        raise
    except Exception as err:
        log.error("Failed to get wallet balances: %s", err)
        raise

    available_byc_amount = balances["byc"]

    # get OKX xch balance
    try:
        response = await accountAPI.get_account_balance(
            ccy=collateral_symbol,
        )
    except Exception as err:
        # keep trying in case of error as we need to close our position
        log.error("Failed to get OKX account balance")
        raise

    if not response["code"] == "0":
        # Failed to receive OKX account balance
        raise ValueError("Failed to get OKX account balance. Response: %s", json.dumps(response))
    if not len(response["data"]) == 1:
        raise ValueError("Unexpected length of response['data'] list. Expected 1, got %s. Response: %s", len(response["data"]), json.dumps(response))
    if not len(response["data"][0]["details"]) == 1:
        raise ValueError("Unexpected length of response['data'][0]['details'] list. Expected 1, got %s. Response: %s", len(response["data"][0]["details"]), json.dumps(response))

    available_xch_balance = float(response["data"][0]["details"][0]["cashBal"]) # in XCH

    log.info("Got OKX account balance: %s %s", available_xch_balance, collateral_symbol)

    log.info(
        "Vault %s has: collateral=%s %s  debt=%s %s  available amount=%s %s  max bid amount=%s %s",
        vault_name, collateral/MOJOS_PER_XCH, collateral_symbol, debt/MCAT, stablecoin_symbol,
        available_byc_amount/MCAT, stablecoin_symbol, max_byc_amount_to_bid/MCAT, stablecoin_symbol
    )

    bid_amount = min(
        available_byc_amount, # don't bid more than available in wallet
        max_byc_amount_to_bid, # don't bid more than necessary according to vault state (amount of debt & collateral)
        int(available_xch_balance * auction_price * 1000 / PRICE_PRECISION), # don't bid more than can be hedged
    )
    if MAX_BID_AMOUNT is not None:
        bid_amount = min(bid_amount, MAX_BID_AMOUNT)

    try:
        log.info("Getting bid info for vault %s with bid amount %s", vault_name, bid_amount)
        bid_info = await rpc_client.upkeep_vaults_bid(vault_name, amount=bid_amount, info=True)
    except httpx.HTTPStatusError as err:
        log.error("Failed to get liquidation auction bid info for bid amount %s due to HTTPStatusError: %s", bid_amount, err)
        raise
    except httpx.ReadTimeout as err:
        log.error("Failed to get liquidation auction bid info for bid amount %s due to ReadTimeout: %s", bid_amount, err)
        raise
    except ValueError as err:
        log.error("Failed to get liquidation auction bid info for bid amount %s due to ValueError: %s", bid_amount, err)
        raise
    except Exception as err:
        log.error("Failed to liquidation auction bid info for bid amount %s: %s", bid_amount, err)
        raise

    if not bid_info["action_executable"]:
        min_byc_amount_to_bid = bid_info["min_byc_amount_to_bid"]
        if available_byc_amount < min_byc_amount_to_bid:
            log.info(
                "Cannot place bid of amount %s m%s in liquidation auction for vault %s: Amount available in wallet is less than min amount to bid (%s < %s)",
                bid_amount, stablecoin_symbol, vault_name, available_byc_amount, min_byc_amount_to_bid
            )
        else:
            log.info("Cannot place bid of amount %s m%s in liquidation auction for vault %s", bid_amount, stablecoin_symbol, vault_name)
        return BidFail.NOT_POSSIBLE

    collateral_to_receive = bid_info["collateral_to_receive"]/MOJOS_PER_XCH
    leftover_collateral = bid_info["leftover_collateral"]/MOJOS_PER_XCH

    log.info(
        "Can buy %.12f %s at %.2f %s for %.3f %s in liquidation auction, leaving %.12f of %.12f %s in collateral",
        collateral_to_receive, collateral_symbol, auction_price/PRICE_PRECISION, f"{collateral_symbol}/{stablecoin_symbol}",
        bid_amount/MCAT, stablecoin_symbol, leftover_collateral, collateral/MOJOS_PER_XCH, collateral_symbol,
    )

    collateral_to_sell = min(collateral_to_receive, available_xch_balance)#TODO: take into account OKX fees # including available_xch_balance again here due to potential rounding errors in bid_amount calculation

    hedge_price, _, hedge_volume = okx_order_book.price("sell", collateral_to_sell, True)
    hedge_amount = hedge_price * hedge_volume
    log.info(f"Can sell {collateral_to_receive:.12f} {collateral_symbol} at {hedge_price} {price_symbol} for {hedge_amount} {proxy_symbol} (hedge amount) on OKX")

    if hedge_amount <= bid_amount * (1 + MARGIN)/MCAT:
        log.info(f"Hedge amount does not exceed {(1+MARGIN)*100:.1f}% of bid amount ({hedge_amount} {stablecoin_symbol} <= {bid_amount * (1+MARGIN)/MCAT} {stablecoin_symbol}). Risk too high. Not placing a bid in liquidation auction")
        return BidFail.NOT_PROFITABLE
    else:
        log.info(f"Hedge amount is greater than {(1+MARGIN)*100:.1f}% of bid amount ({hedge_amount} {stablecoin_symbol} > {bid_amount * (1+MARGIN)/MCAT} {stablecoin_symbol}. Placing bid of {bid_amount/MCAT:.3f} {stablecoin_symbol} in liquidation auction")
        # bid for collateral
        try:
            response = await rpc_client.upkeep_vaults_bid(vault_name, bid_amount)
        except httpx.HTTPStatusError as err:
            log.error("Failed to place liquidation auction bid for vault %s due to HTTPStatusError: %s", vault_name, err)
            raise
        except httpx.ReadTimeout as err:
            log.error("Failed to place liquidation auction bid for vault %s due to ReadTimeout: %s", vault_name, err)
            raise
        except ValueError as err:
            log.error("Failed to place liquidation auction bid for vault %s due to ValueError: %s", vault_name, err)
            raise
        except Exception as err:
            log.error("Failed to place liquidation auction bid for vault %s: %s", vault_name, err)
            raise

        if not response["status"] == "success":
            raise ValueError("Failed to place liquidation auction bid for vault %. Response code: %s", vault_name, response["success"])

        # hedge on OKX
        retry_delay = 2
        max_retries = 30
        cnt = 0
        while cnt < max_retries:
            cnt += 1
            log.info("Attempt no. %s/%s to place market sell order for %s %s on OKX", cnt, max_retries, hedge_volume, collateral_symbol)
            try:
                response = await tradeAPI.place_order(
                    instId=market_symbol,
                    tdMode='cash',
                    side='sell',
                    ordType='market',
                    tgtCcy='base_ccy', # currency in which size is measured
                    sz="{:.{}f}".format(hedge_volume, base_decimals),
                )
            except Exception as err:
                # keep trying in case of error as we need to close our position
                log.error("Failed to place market sell order on OKX: %s. Retrying in %s seconds", str(err), retry_delay)
                await asyncio.sleep(retry_delay)
                continue

            if not response["code"] == "0":
                # OKX failed to place order
                log.error("Failed to place market sell order on OKX. Response: %s", json.dumps(response))
                await asyncio.sleep(retry_delay)
                continue
            if not len(response["data"]) == 1:
                log.warning("Unexpected length of response['data'] list. Expected 1, got %s. Response: %s", len(response["data"]), json.dumps(response))

            ordId = response["data"][0]["ordId"]
            log.info("Successfully placed market sell order on OKX. OrdId: %s", ordId) # Response: %s", json.dumps(response))
            break

        # check that we sold expected amount and calculate PnL
        retry_delay = 2
        max_retries = 5
        cnt = 0
        while cnt < max_retries:
            cnt += 1
            log.info("Attempt no. %s/%s to get order info for ordID %s", cnt, max_retries, ordId)
            try:
                response = await tradeAPI.get_order(instId=market_symbol, ordId=ordId)
            except Exception as err:
                # keep trying in case of error as we need to close our position
                log.error("Failed to get OKX order ordId %s", ordId)
                await asyncio.sleep(retry_delay)
                continue

            if not response["code"] == "0":
                # failed to get order
                log.error("Failed to get OKX order ordId %s. Response: %s", ordId, json.dumps(response))
                await asyncio.sleep(retry_delay)
                continue
            if not len(response["data"]) == 1:
                log.warning("Unexpected length of response['data'] list. Expected 1, got %s. Response: %s", len(response["data"]), json.dumps(response))

            state = response["data"][0]["state"]
            total_fill_volume = float(response["data"][0]["accFillSz"]) # given in base currency
            last_fill_price = float(response["data"][0]["fillPx"])
            avg_fill_price = float(response["data"][0]["avgPx"]) if response["data"][0]["avgPx"] != "" else None
            okx_fee = float(response["data"][0]["fee"]) # accumulated fee and rebate in quote currency
            fee_ccy = response["data"][0]["feeCcy"]
            if not state == "filled":
                log.warning("Market sell order was not filled. State: %s", state)
            if not fee_ccy == proxy_symbol:
                log.warning("Market sell order fees were charged in %s, expected %s", fee_ccy, proxy_symbol)
            if not abs(total_fill_volume - hedge_volume) < 10**(-base_decimals):
                log.warning("Filled volume does not equal hedge volume (%s != %s)", total_fill_volume, hedge_volume)

            log.info(
                "Market sell order was filled. Volume: %s %s. Avg price: %s %s. Lowest price: %s %s",
                total_fill_volume, collateral_symbol, avg_fill_price, price_symbol, last_fill_price, price_symbol
            )

            quote_delta = bid_amount/MCAT - total_fill_volume * avg_fill_price
            base_delta = collateral_to_receive - total_fill_volume + okx_fee # LATER: add on-chain tx fee
            pnl = quote_delta + base_delta * okx_order_book.mid_price()

            log.info("PnL: %.2f USD. Remaining %s position: %.3f. Remaining stablecoin position: %.3f", pnl, collateral_symbol, base_delta, quote_delta)
            break

        return BidFail.NONE
        # reconcile balances
        # LATER: OKX may adjust size of market order if user doesn't have enough funds.
        #   See banAmend parameter: https://my.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order

        # recycle capital
        # LATER: USDT -> USDC --transfer to Base-> USDC.b --warp.green-> wUSDC.b -> BYC


async def run_liquidation_bid_bot():

    parser = argparse.ArgumentParser(description="Liquidation bid bot for Circuit protocol")
    parser.add_argument("-e", "--environment", choices=["demo", "live"], default="demo")
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Debug output from OKX client")
    args = parser.parse_args()

    if args.environment == "demo":
        trade_env = TradeEnv.DEMO
        collateral_symbol = "XRP" # Using XRP as OKX does not have a XCH/USDT spot market in demo environment
        okx_websocket_url = "wss://wseeapap.okx.com:8443/ws/v5/public"
        key = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_DEMO_TRADING_PASSPHRASE")
    elif args.environment == "live":
        trade_env = TradeEnv.LIVE
        collateral_symbol = "XCH" # CircuitDAO collateral asset
        okx_websocket_url = "wss://wseea.okx.com:8443/ws/v5/public" # (wss://ws.okx.com:8443/ws/v5/public)
        key = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_KEY")
        secret = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_SECRET")
        passphrase = os.getenv("VOLTAGE_OKX_API_LIVE_TRADING_PASSPHRASE")

    log.info("Running liquidation bid bot in %s environment", trade_env.name)

    stablecoin_symbol = "BYC" # Circuit stablecoin (BYC)

    # BYC proxy asset to be used for hedging
    # Note: on OKX, instID uniquely identifies the market. No need to know the instrument type.
    proxy_instrument = SPOT
    proxy_symbol = "USDT"

    # OKX symbol corresponding to base and quote
    market_symbol, price_symbol = get_okx_symbols(collateral_symbol, proxy_symbol)

    uquote_symbol = "USD" # ultimate quote currency

    # Subscribe to order book
    okx_order_book = OkxOrderBook(market_symbol, uquote_symbol, okx_websocket_url, verbose=args.debug)
    await okx_order_book.connect()
    await okx_order_book.subscribe()

    # Instantiate trade API
    if not all([key, secret, passphrase]):
        ValueError("Must provide key, secret and passphrase to connect to OKX trade API")
    tradeAPI = AsyncTradeAPI(key, secret, passphrase, flag=trade_env.value, debug=args.debug)
    accountAPI = AsyncAccountAPI(key, secret, passphrase, flag=trade_env.value, debug=args.debug)

    base_decimals = 4 # LATER: can we get this from OKX API in case it ever changes?
    price_decimals = 4 # LATER: can we get this from OKX API in case it ever changes?

    while True:

        await rpc_client.set_fee_per_cost()

        try:
            state = await rpc_client.upkeep_state(vaults=True)
        except httpx.HTTPStatusError as err:
            log.error("Failed to get state of vaults due to HTTPStatusError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except httpx.ReadTimeout as err:
            log.error("Failed to get state of vaults due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get state of vaults due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get state of vaults: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        vaults_in_liquidation = state.get("vaults_in_liquidation", [])

        if not vaults_in_liquidation:
            log.info("No vaults in liquidation. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info("%s vaults in liquidation. Bidding in liquidation auctions", len(vaults_in_liquidation))

        liquidation_tasks = []
        for vault in vaults_in_liquidation:
            task = asyncio.create_task(
                liquidate_vault(
                    vault["name"], rpc_client, okx_order_book, tradeAPI, accountAPI, base_decimals, price_decimals,
                    collateral_symbol, stablecoin_symbol, proxy_symbol, market_symbol, price_symbol,
                )
            )
            liquidation_tasks.append(task)

        bid_failed = 0
        bid_not_possible = 0
        bid_not_profitable = 0

        results = await asyncio.gather(*liquidation_tasks, return_exceptions=True) # LATER: impose a max run time on liquidation tasks so we don't get stuck?
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                bid_failed += 1
                log.error(f"Liquidation task {i} (vault {vaults_in_liquidation[i]['name']}) failed: {result}")
            elif result == BidFail.NOT_POSSIBLE:
                bid_not_possible += 1
                log.info(f"Liquidation task {i} (vault {vaults_in_liquidation[i]['name']}) succeeded, but vault not liquidated: {result}")
            elif result == BidFail.NOT_PROFITABLE:
                bid_not_profitable += 1
                log.info(f"Liquidation task {i} (vault {vaults_in_liquidation[i]['name']}) succeeded, but vault not liquidated: {result}")
            else:
                log.info(f"Liquidation task {i} (vault {vaults_in_liquidation[i]['name']}) succeeded: {result}")

        if bid_failed > 0 or bid_not_possible > 0:
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        await asyncio.sleep(RUN_INTERVAL)


def main():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_liquidation_bid_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down liquidation bid bot")
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == '__main__':
    main()
