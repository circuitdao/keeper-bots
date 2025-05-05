import argparse
import asyncio
import os
import time
import random
import math
import httpx
import yaml
import logging.config

from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger(__name__)


STATUTE_TREASURY_MINIMUM_DELTA = 20
TRANSFER_CHECK_INTERVAL = 6 * 60 * 60


async def run_bot():
    parser = argparse.ArgumentParser(description="Circuit reference stability fee transfer bot")
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

    while True:

        # get vaults
        try:
            vaults = await rpc_client.upkeep_vaults_list()
        except httpx.ReadTimeout as err:
            print("Failed to get vaults list due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to get vaults list", err)
            await asyncio.sleep(10)
            continue

        non_seized_vaults = [v for v in vaults if not (v["in_liquidation"] or v["in_bad_debt"])]
        if len(non_seized_vaults) == 0:
            log.info("Not transferring any Stability Fees. No non-seized collateral vaults")
            await asyncio.sleep(TRANSFER_CHECK_INTERVAL)
            continue

        vault = non_seized_vaults[0]

        # transfer SFs
        try:
            response = await rpc_client.upkeep_vaults_transfer(COIN_NAME=vault["name"])
        except httpx.ReadTimeout as err:
            log.error("Failed to transfer SFs due to ReadTimeout:", err)
            await asyncio.sleep(10)
            continue
        except httpx.HTTPStatusError as err:
            log.error("Failed to transfer SFs due to HTTPStatusError: %s", str(err))
            await asyncio.sleep(10)
            continue
        except Exception as err:
            log.error("Failed to transfer SFs:", err)
            await asyncio.sleep(10)
            continue

        #print(f"SF transfer tx broadcast status: {response['status']}")

        #print(f"{response=}")
        final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
        coin_name = final_bundle.additions()[0].name().hex()

        ## wait for transaction to be confirmed
        #try:
        #    print("Waiting for confirmation...")
        #    await rpc_client.wait_for_confirmation(final_bundle)
        #except ValueError as err:
        #    print("SF transfer transaction failed to confirm")
        #    await asyncio.sleep(10)
        #    continue

        #print("Confirmed")
        log.info("New collateral vault coin:", coin_name)
        log.info("All new coins:", [coin.name().hex() for coin in final_bundle.additions()])

        await asyncio.sleep(TRANSFER_CHECK_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
