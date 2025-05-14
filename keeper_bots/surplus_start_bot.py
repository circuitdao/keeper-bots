##
## Bot that starts CircuitDAO surplus auctions
##
## The bot:
## 1) monitors treasury
## 2) triggers surplus auction

import os
import asyncio
import argparse
import httpx
import yaml
import logging.config

from circuit_cli.client import CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("surplus_start_bot")


RUN_INTERVAL = 1 * 60
CONTINUE_DELAY = 10


async def run_surplus_start_bot():

    parser = argparse.ArgumentParser(description="Circuit reference Surplus Auction start bot")
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
        default=os.environ.get("PRIVATE_KEY"),
        help="Private key for your coins",
    )

    args = parser.parse_args()

    if not args.private_key:
        raise ValueError("No private key provided")

    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    while True:

        try:
            treasury = await rpc_client.upkeep_treasury_show()
        except httpx.ReadTimeout as err:
            log.error("Failed to show Treasury due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to show Treasury due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to show Treasury: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if treasury["can_start_surplus_auction"]:

            # start a surplus auction
            try:
                await rpc_client.upkeep_surplus_start()
            except httpx.ReadTimeout as err:
                log.error("Failed to start Surplus Auction due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to start Surplus Auction due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to start Surplus Auction: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


if __name__ == '__main__':

    asyncio.run(run_surplus_start_bot())

