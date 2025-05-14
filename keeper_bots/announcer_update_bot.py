import argparse
import asyncio
import os
import time
import random
import math
import httpx
import yaml
import logging.config
from datetime import datetime

from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient

from okx_feed import OkxFeed

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("announcer_update_bot")


PRICE_PRECISION = 10**2
PRICE_UPDATE_THRESHOLD_BPS = 0

RUN_INTERVAL = 1 * 60
CONTINUE_DELAY = 10


#async def fetch_gateio_price():
#    url = "https://api.gate.io/api2/1/ticker/xch_usdt"
#    async with httpx.AsyncClient() as client:
#        gateio_response = await client.get(url)
#        if gateio_response.status_code == 200:
#            gateio_data = gateio_response.json()
#            return int(float(gateio_data.get("last")) * 100)
#        else:
#            raise ValueError(f"Failed to fetch price from gate.io: {gateio_response.text}")


async def run_announcer():
    parser = argparse.ArgumentParser(description="Circuit reference announcer price update bot")
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default="http://localhost:8000",
    )
    parser.add_argument(
        "--okx-url",
        type=str,
        help="Base URL of the OKX API",
        default="wss://ws.okx.com:8443/ws/v5/public",
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument(
        "--private-key", "-p",
        type=str,
        help="Private key for your coins",
        default=os.environ.get("PRIVATE_KEY"),
    )
    parser.add_argument("-s", "--startup-window", type=int, default=0, help="Length of start-up window in seconds")
    parser.add_argument("-a", "--average-window", type=int, default=3600, help="Length of window over which to calculate volume-weighted average price in seconds")

    args = parser.parse_args()

    if not args.private_key:
        raise ValueError("No private key provided")

    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    # Connect to OKX price feed
    sym = "XCH-USDT"
    uquote = "USD" # ultimate quote currency
    startup_window_length = args.startup_window
    window_length = args.average_window

    if startup_window_length > window_length:
        raise ValueError("Start-up window must not be longer than averaging window")

    feed = OkxFeed(sym, uquote, args.okx_url, startup_window_length=startup_window_length, window_length=window_length, verbose=False)

    async with feed as okx_feed:

        cnt = 0 # Counter to artificially reduce oracle price over time for testing purposes

        while True:

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

            #print(f"{announcers=}")
            if len(announcers) < 1:
                log.error("No announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            approved_announcers = [x for x in announcers if x["approved"]]
            if len(approved_announcers) < 1:
                log.warning("No approved announcer found")
                if len(announcers) > 1:
                    log.error("More than one unapproved announcer found")
                announcer = announcers[0]
                log.info("Found an unapproved announcer. Name: %s  LauncherID: %s", announcer['name'], announcer['launcher_id'])
            else:
                if len(approved_announcers) > 1:
                    log.error("More than one approved announcer found")
                announcer = approved_announcers[0]
                log.info("Found an approved announcer. Name: %s  LauncherID: %s", announcer['name'], announcer['launcher_id'])

            # get latest volume-weighted XCH/USD price
            try:
                price = await okx_feed.get_price()
            except Exception as err: #(TypeError, ValueError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError):
                log.error("Failed to fetch latest price:", err)
                price = announcer["price"] # if failed to get price from feed, re-publish announcer price to prevent expiry
                log.info("Using existing price: %.2f", price/PRICE_PRECISION)
            else:
                if math.isnan(price):
                    price = announcer["price"] # if still ramping up, re-publish announcer price to prevent expiry
                    log.info("Still ramping up, using existing price: %.2f", price/PRICE_PRECISION)
                else:
                    #price = int(PRICE_PRECISION * price * 0.99**cnt) # reduce oralce price by 1% per iteration #random.randint(10000, 12000)  # await fetch_okx_price()
                    price = int(PRICE_PRECISION * price)
                    log.info("Fetched latest price: %.2f", price/PRICE_PRECISION)
                    cnt += 1

            # update announcer price

            if price < 0:
                json_msg = {
                    "error_message": "XCH/USD market price is negative",
                    "announcer_price": announcer["price"],
                    "market_price": price,
                }
                log.error("Failed to update announcer price", extra=json_msg)
            elif price == 0:
                json_msg = {
                    "warning_message": "XCH/USD market price is 0",
                    "announcer_price": announcer["price"],
                    "market_price": price,
                }
                log.warning("XCH/USD market price is 0", extra=json_msg)
            elif abs(announcer["price"]/price - 1) <= PRICE_UPDATE_THRESHOLD_BPS/10000.0:
                #json_msg = {
                #    "info_message": "Price update threshold not exceeded",
                #    "announcer_price": announcer["price"],
                #    "market_price": price,
                #    "PRICE_UPDATE_THRESHOLD_BPS": PRICE_UPDATE_THRESHOLD_BPS,
                #}
                #log.info("Not updating announcer", extra=json_msg)
                log.info("Not updating announcer. Price update threshold not reached")
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            log.info("Updating announcer. Setting price to %.2f", price/PRICE_PRECISION)

            try:
                response = await rpc_client.announcer_update(price, COIN_NAME=announcer["name"])
                #print(f"Announcer update tx broadcast status: {response['status']}")
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

            log.info("Updated announcer. Price set to %.2f", price/PRICE_PRECISION)

            final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
            coin_name = final_bundle.additions()[0].name().hex()

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
