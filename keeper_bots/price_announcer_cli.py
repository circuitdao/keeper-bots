import argparse
import asyncio
import os
import random
import math

import httpx
from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient

from okx_feed import OkxFeed

PRICE_PRECISION = 10**2

"""
async def fetch_okx_price():
    #Fetches the latest price of a cryptocurrency from OKX API v5.
    #:return: The latest price as a float, or None if an error occurs.
    url = "https://www.okx.com/api/v5/market/ticker?instId=XCH-USDT"
    headers = {"Accept": "application/json"}

    async with httpx.AsyncClient() as http_client:
        try:
            okx_response = await http_client.get(url, headers=headers)
            okx_response.raise_for_status()  # Raises an error for bad responses
            okx_data = okx_response.json()
            return int(float(okx_data["data"][0]["last"]) * 100)
        except Exception as e:
            print(f"Error fetching crypto price: {e}")
            return None
"""

async def fetch_gateio_price():
    url = "https://api.gate.io/api2/1/ticker/xch_usdt"
    async with httpx.AsyncClient() as client:
        gateio_response = await client.get(url)
        if gateio_response.status_code == 200:
            gateio_data = gateio_response.json()
            return int(float(gateio_data.get("last")) * 100)
        else:
            raise ValueError(f"Failed to fetch price from gate.io: {gateio_response.text}")


async def run_announcer():
    parser = argparse.ArgumentParser(description="Circuit reference price announcer CLI tool")
    parser.add_argument(
        "--base-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default="http://localhost:8000",
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument(
        "--private-key", "-p", type=str, default=os.environ.get("PRIVATE_KEY"), help="Private key for your coins"
    )
    args = parser.parse_args()
    rpc_client = CircuitRPCClient(args.base_url, args.private_key)

    # Connect to OKX price feed
    sym = "XCH-USDT"
    uquote = "USD" # ultimate quote currency
    startup_window_length = 10
    window_length = 3600

    if startup_window_length > window_length:
        raise ValueError("Start-up window must not be longer than full window")

    okx_feed = OkxFeed(sym, uquote, startup_window_length, window_length, verbose=False)

    okx_feed.connect()
    okx_feed.subscribe()

    cnt = 0 # Counter to artificially reduce oracle price over time for testing purposes
    while True:

        try:
            data = await rpc_client.announcer_list()
        except httpx.ReadTimeout as err:
            print("Failed to get announcer list due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to get announcer list", err)
            await asyncio.sleep(10)
            continue

        if len(data) == 0:
            raise ValueError("No announcers found")
        coin_name = [x for x in data if x["is_approved"]][0]["name"]

        # Get latest volume-weighted XCH/USD price
        try:
            price = await okx_feed.get_price()
        except Exception as err: #(TypeError, ValueError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError):
            print("Failed to fetch price, skipping.", err)
            await asyncio.sleep(10)
            continue

        print(f"FETCHED VOLUME-WEIGHTED MARKET PRICE: {price}")
        if math.isnan(price):
            await asyncio.sleep(10)
            continue
        #price = int(PRICE_PRECISION * price * 0.99**cnt) # reduce oralce price by 1% per iteration #random.randint(10000, 12000)  # await fetch_okx_price()
        price = int(PRICE_PRECISION * price)
        cnt += 1

        # Mutate announcer
        print("Mutating announcer", coin_name, "to set price to", price)
        try:
            data = await rpc_client.announcer_mutate(coin_name, price=price)
        except httpx.ReadTimeout as err:
            print("Failed to mutate announcer due to ReadTimeout", err)
            await asyncio.sleep(60)
            continue
        except ValueError as err:
            print("Failed to mutate announcer due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to mutate announcer", err)
            await asyncio.sleep(10)
            continue

        print("Got back data", data)

        final_bundle: SpendBundle = SpendBundle.from_json_dict(data["bundle"])
        coin_name = final_bundle.additions()[0].name().hex()

        # Wait for transaction to be confirmed
        try:
            print("Waiting for confirmation...")
            await rpc_client.wait_for_confirmation(final_bundle)
        except ValueError as err:
            print("Announcer mutate transaction failed")
            await asyncio.sleep(10)
            continue

        print("Confirmed")
        print("New announcer coin name:", coin_name)
        print("All new coins:", [coin.name().hex() for coin in final_bundle.additions()])

        # Try to update oracle
        await rpc_client.upkeep_sync()
        print("Updating oracle")
        try:
            data = await rpc_client.oracle_update()
        except httpx.ReadTimeout as err:
            print("Failed to update Oracle due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except ValueError as err:
            print("Failed to update Oracle due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to update Oracle", err)
            await asyncio.sleep(10)
            continue

        # Update statutes price if Oracle update was successful
        try:
            data = await rpc_client.statutes_update_price()
        except httpx.ReadTimeout as err:
            print("Failed to update Statutes Price due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except ValueError as err:
            print("Failed to update Statutes Price due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to update Statutes Price", err)
            await asyncio.sleep(10)
            continue

        print("Updated Statutes Price. Result: ", data)
        await asyncio.sleep(60) # 15 * 60)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
