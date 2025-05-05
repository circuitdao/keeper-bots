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


STATUTE_TREASURY_REBALANCE_DELTA_PCT = 21
REBALANCE_CHECK_INTERVAL = 15 * 60 # check every quarter hour


async def run_bot():
    parser = argparse.ArgumentParser(description="Circuit reference treasury rebalance bot")
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

        # rebalance treasury
        try:
            response = await rpc_client.upkeep_treasury_rebalance()
        except httpx.ReadTimeout as err:
            log.error("Failed to rebalance treasury due to ReadTimeout:", err)
            await asyncio.sleep(10)
            continue
        except httpx.HTTPStatusError as err:
            log.error("Failed to rebalance treasury due to HTTPStatusError: %s", str(err))
            await asyncio.sleep(10)
            continue
        except Exception as err:
            log.error("Failed to rebalance treasury:", err)
            await asyncio.sleep(10)
            continue

        final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])

        log.info("All new coins:", [coin.name().hex() for coin in final_bundle.additions()])

        await asyncio.sleep(REBALANCE_CHECK_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
