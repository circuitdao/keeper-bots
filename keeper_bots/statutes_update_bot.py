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

log = logging.getLogger("statutes_update_bot")


RUN_INTERVAL = 1 * 20
CONTINUE_DELAY = 10


async def run_statutes():
    parser = argparse.ArgumentParser(description="Circuit reference Statutes price update bot")
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
    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    while True:

        # Update Statutes price
        log.info("Updating Statutes Price")

        try:
            data = await rpc_client.statutes_update()
        except httpx.ReadTimeout as err:
            log.error("Failed to update Statutes Price due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to update Statutes Price due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to update Statutes Price: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Updated Statutes Price")

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_statutes())


if __name__ == "__main__":
    main()
