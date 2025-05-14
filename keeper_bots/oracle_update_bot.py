import argparse
import asyncio
import os
#import time
#import random
#import math
import httpx
import yaml
import logging.config
#from datetime import datetime

#from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("oracle_update_bot")


RUN_INTERVAL = 1 * 20
CONTINUE_DELAY = 10


async def run_oracle():
    parser = argparse.ArgumentParser(description="Circuit reference Oracle price update bot")
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default="http://localhost:8000",
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument(
        "--private-key", "-p",
        type=str,
        help="Private key for your coins",
        default=os.environ.get("PRIVATE_KEY"),
    )

    args = parser.parse_args()

    if not args.private_key:
        raise ValueError("No private key provided")

    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    while True:

        # update oracle
        log.info("Updating Oracle price")
        #await rpc_client.upkeep_rpc_sync()

        try:
            data = await rpc_client.oracle_update()
        except httpx.ReadTimeout as err:
            log.error("Failed to update Oracle due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to update Oracle due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to update Oracle: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Updated Oracle price")

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_oracle())


if __name__ == "__main__":
    main()
