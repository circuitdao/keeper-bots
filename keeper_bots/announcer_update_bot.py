import asyncio
import os
import time
import random
import math
import httpx
import yaml
import logging.config
from datetime import datetime, timedelta
from dotenv import load_dotenv

from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient
from hsms.cmds.hsms import XCH_PER_MOJO

from keeper_bots.okx_feed import OkxFeed
from keeper_bots.utils import set_dotenv_variable

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("announcer_update_bot")


# LATER: get from RPC invariants
MAX_TX_BLOCK_TIME = 120
PRICE_PRECISION = 10 ** 2


async def run_announcer():

    try:
        load_dotenv(override=True)
    except Exception as err:
        log.error("Failed to load .env: %s", str(err))

    rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
    private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
    RUN_INTERVAL = int(os.getenv("ANNOUNCER_UPDATE_RUN_INTERVAL")) # Frequency (in seconds) with which to run bot
    CONTINUE_DELAY = int(os.getenv("ANNOUNCER_UPDATE_CONTINUE_DELAY")) # Wait (in seconds) before bot runs again after a failed run
    TTL_BUFFER = int(os.getenv("ANNOUNCER_UPDATE_TTL_BUFFER")) # Update price no later than on next run after price expiry minus TTL buffer has passed
    UPDATE_THRESHOLD_BPS = int(os.getenv("ANNOUNCER_UPDATE_UPDATE_THRESHOLD_BPS")) # Update price as soon as it has changed more than specified amount of bps
    startup_window = int(os.getenv("ANNOUNCER_UPDATE_STARTUP_WINDOW")) # Length of start-up window in seconds
    average_window = int(os.getenv("ANNOUNCER_UPDATE_AVERAGE_WINDOW")) # Length of window over which to calculate volume-weighted average price in seconds

    if not rpc_url:
        raise ValueError("No URL found at which Circuit RPC server can be reached")
    if not private_key:
        raise ValueError("No master private key found")
    if startup_window > average_window:
        raise ValueError("Start-up window must not be longer than averaging window")

    # adjust TTL buffer by taking into account timestamp flexibility in penalize operation,
    # run interval and continue delay, as well as possibility that runs may fail
    TTL_BUFFER_ADJ = max(TTL_BUFFER, MAX_TX_BLOCK_TIME + max(RUN_INTERVAL, CONTINUE_DELAY) + (2 * CONTINUE_DELAY) + 1)
    log.info("Set adjusted TTL buffer: %s, %s", TTL_BUFFER, TTL_BUFFER_ADJ)

    # define adjusted update threshold. this will be the min of env var and Statute ORACLE_PRICE_UPDATE_DELTA_BPS
    UPDATE_THRESHOLD_BPS_ADJ = UPDATE_THRESHOLD_BPS # setting default value
    log.info("Set default adjusted update threshold [bps]: %s", UPDATE_THRESHOLD_BPS_ADJ)

    rpc_client = CircuitRPCClient(
        rpc_url,
        private_key,
        os.getenv("ADD_SIG_DATA"), # Additional signature data (depends on network)
        int(os.getenv("FEE_PER_COST")), # Fee per cost for transactions
    )

    # Connect to OKX price feed
    sym = "XCH-USDT"
    uquote = "USD"  # ultimate quote currency

    feed = OkxFeed(
        sym, uquote,
        os.getenv("OKX_URL"), # Base URL of the OKX API
        startup_window_length=startup_window,
        window_length=average_window,
        verbose=False
    )

    async with feed as okx_feed:

        cnt = 0  # Counter to artificially reduce oracle price over time for testing purposes

        while True:

            # update parameters
            try:
                # load env variables, overriding existing values (if any)
                load_dotenv(override=True)
            except Exception as err:
                # log error and continue with existing parameters
                log.error("Failed to load .env: %s", str(err))
            else:
                # update parameters according to env vars loaded
                fee_per_cost = int(os.getenv("FEE_PER_COST"))
                if rpc_client.fee_per_cost != fee_per_cost:
                    log.info("Updating fee per cost: %s -> %s", rpc_client.fee_per_cost, fee_per_cost)
                    rpc_client.fee_per_cost = fee_per_cost
                run_interval = int(os.getenv("ANNOUNCER_UPDATE_RUN_INTERVAL"))
                if RUN_INTERVAL != run_interval:
                    log.info("Updating run interval: %s -> %s", RUN_INTERVAL, run_interval)
                    RUN_INTERVAL = run_interval
                    ttl_buffer_adj = max(TTL_BUFFER, MAX_TX_BLOCK_TIME + max(RUN_INTERVAL, CONTINUE_DELAY) + (2 * RUN_INTERVAL + 1))
                    log.info("Updating adjusted TTL buffer: %s -> %s", TTL_BUFFER_ADJ, ttl_buffer_adj)
                    TTL_BUFFER_ADJ = ttl_buffer_adj
                continue_delay = int(os.getenv("ANNOUNCER_UPDATE_CONTINUE_DELAY"))
                if CONTINUE_DELAY != continue_delay:
                    log.info("Updating continue delay: %s -> %s", CONTINUE_DELAY, continue_delay)
                    CONTINUE_DELAY = continue_delay
                    ttl_buffer_adj = max(TTL_BUFFER, MAX_TX_BLOCK_TIME + max(RUN_INTERVAL, CONTINUE_DELAY) + (2 * RUN_INTERVAL + 1))
                    log.info("Updating adjusted TTL buffer: %s -> %s", TTL_BUFFER_ADJ, ttl_buffer_adj)
                    TTL_BUFFER_ADJ = ttl_buffer_adj
                ttl_buffer = int(os.getenv("ANNOUNCER_UPDATE_TTL_BUFFER"))
                if TTL_BUFFER != ttl_buffer:
                    log.info("Updating TTL buffer: %s -> %s", TTL_BUFFER, ttl_buffer)
                    TTL_BUFFER = ttl_buffer
                    ttl_buffer_adj = max(TTL_BUFFER, MAX_TX_BLOCK_TIME + max(RUN_INTERVAL, CONTINUE_DELAY) + (2 * RUN_INTERVAL + 1))
                    log.info("Updating adjusted TTL buffer: %s -> %s", TTL_BUFFER_ADJ, ttl_buffer_adj)
                    TTL_BUFFER_ADJ = ttl_buffer_adj
                update_threshold_bps = int(os.getenv("ANNOUNCER_UPDATE_UPDATE_THRESHOLD_BPS"))
                if UPDATE_THRESHOLD_BPS != update_threshold_bps:
                    log.info("Updating update threshold: %s -> %s", UPDATE_THRESHOLD_BPS, update_threshold_bps)
                    UPDATE_THRESHOLD_BPS = update_threshold_bps
                startup_window = int(os.getenv("ANNOUNCER_UPDATE_STARTUP_WINDOW"))
                if feed.startup_window_length.total_seconds() != startup_window:
                    log.info("Updating startup window: %s -> %s", feed.startup_window_length.total_seconds(), startup_window)
                    feed.startup_window_length = timedelta(seconds=startup_window)
                average_window = int(os.getenv("ANNOUNCER_UPDATE_AVERAGE_WINDOW"))
                if feed.window_length.total_seconds() != average_window:
                    log.info("Updating average window: %s -> %s", feed.window_length.total_seconds(), average_window)
                    feed.window_length = timedelta(seconds=average_window)

            # get Statutes
            try:
                statutes = await rpc_client.statutes_list()
            except Exception as err:
                # log error and continue with existing parameter
                log.error("Failed to get Statutes: %s", str(err))
            else:
                try:
                    statutes_update_threshold_bps = int(statutes["implemented_statutes"]["ORACLE_PRICE_UPDATE_DELTA_BPS"])
                except Exception as err:
                    # log error and continue with existing parameter
                    log.error("Failed to get Statute ORACLE_PRICE_UPDATE_DELTA_BPS: %s", str(err))
                else:
                    update_threshold_bps_adj = min(UPDATE_THRESHOLD_BPS, statutes_update_threshold_bps)
                    if update_threshold_bps_adj != UPDATE_THRESHOLD_BPS_ADJ:
                        log.info("Updating adjusted update threshold [bps]: %s -> %s", UPDATE_THRESHOLD_BPS_ADJ, update_threshold_bps_adj)
                        UPDATE_THRESHOLD_BPS_ADJ = update_threshold_bps_adj

            # show announcer
            try:
                announcers = await rpc_client.announcer_show()
            except httpx.ReadTimeout as err:
                log.error("Failed to show announcer due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to show announcer: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            # print(f"{announcers=}")
            if len(announcers) < 1:
                log.error("No announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            approved_announcers = [x for x in announcers if x["approved"]]
            if len(approved_announcers) < 1:
                log.warning("No approved announcer found")
                if len(announcers) > 1:
                    log.warning("More than one unapproved announcer found")
                announcer = announcers[0]
                log.info("Found an unapproved announcer. Name: %s  LauncherID: %s", announcer['name'],
                         announcer['launcher_id'])
            else:
                if len(approved_announcers) > 1:
                    log.error("More than one approved announcer found")
                announcer = approved_announcers[0]
                log.info("Found an approved announcer. Name: %s  LauncherID: %s", announcer['name'],
                         announcer['launcher_id'])

            # get latest volume-weighted XCH/USD price
            try:
                price = await okx_feed.get_price()
            except Exception as err:  # (TypeError, ValueError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError):
                log.error("Failed to fetch latest price:", err)
                price = announcer["price"]  # if failed to get price from feed, re-publish announcer price to prevent expiry
                log.info("Using existing price: %.2f", price / PRICE_PRECISION)
            else:
                if math.isnan(price):
                    price = announcer["price"]  # if still ramping up, re-publish announcer price to prevent expiry
                    log.info("No price received from OKX feed, using existing price: %.2f", price / PRICE_PRECISION)
                else:
                    # price = int(PRICE_PRECISION * price * 0.99**cnt) # reduce oralce price by 1% per iteration #random.randint(10000, 12000)  # await fetch_okx_price()
                    price = int(PRICE_PRECISION * price)
                    log.info("Fetched latest price: %.2f. Current price: %.2f", price / PRICE_PRECISION, announcer["price"] / 100.0)
                    cnt += 1

            # update announcer price
            if price < 0:
                json_msg = {
                    "warning_message": "XCH/USD market price is negative",
                    "announcer_price": announcer["price"],
                    "market_price": price,
                }
                log.error("XCH/USD market price is negative", extra=json_msg)
                price = announcer["price"] # re-publish existing price
            elif price < 1:
                json_msg = {
                    "warning_message": "XCH/USD market price is 0",
                    "announcer_price": announcer["price"],
                    "market_price": price,
                }
                log.warning("XCH/USD market price is non-negative and < 0.01. Setting to 0.01", extra=json_msg)
                price = 1 # set to minimum price allowed by protocol
            elif announcer['expires_in'] > TTL_BUFFER_ADJ and abs(announcer["price"] / float(price) - 1) <= UPDATE_THRESHOLD_BPS_ADJ / 10000.0:
                log.info(
                    "Not updating announcer. Adjusted price update threshold not reached (%.2f%% <= %.2f%%) and sufficiently far from expiry (%ss > %ss)",
                    abs(announcer["price"] / float(price) - 1) * 100,
                    UPDATE_THRESHOLD_BPS_ADJ / 100.0,
                    announcer['expires_in'],
                    TTL_BUFFER_ADJ,
                )
                log.info("Sleeping for %s seconds", RUN_INTERVAL)
                await asyncio.sleep(RUN_INTERVAL)
                continue

            log.info("Updating announcer. Setting price to %.2f XCH/USD", price / PRICE_PRECISION)

            try:
                response = await rpc_client.announcer_update(price, COIN_NAME=announcer["name"], units=True)
            except httpx.ReadTimeout as err:
                log.error("Failed to update announcer due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to update announcer due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to update announcer: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            log.info("Updated announcer. Price set to %.2f XCH/USD", price / PRICE_PRECISION)

            final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
            coin_name = final_bundle.additions()[0].name().hex() # LATER: first one might be a fee coin (if a fee coin was used)

            log.info("New announcer coin: %s", coin_name)
            log.info("All new coins: %s", [coin.name().hex() for coin in final_bundle.additions()])

            # sleep until next run
            log.info("Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
