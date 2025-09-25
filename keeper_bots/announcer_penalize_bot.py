## Announcer penalization bot
##
## The bot:
## 1) retrieves penalizable announcers
## 2) if any, it penalizes those announcers


import asyncio
import os
import httpx
import yaml
import logging.config
from dotenv import load_dotenv

from chia_rs import SpendBundle

from circuit_cli.client import CircuitRPCClient

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("announcer_penalize_bot")


load_dotenv(override=True)


rpc_url = str(os.getenv("RPC_URL"))  # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY"))  # Private master key that controls announcer
add_sig_data = os.getenv("ADD_SIG_DATA")  # Additional signature data (depends on network)
fee_per_cost = int(os.getenv("FEE_PER_COST"))  # Fee per cost for transactions
RUN_INTERVAL = int(os.getenv("ANNOUNCER_PENALIZE_RUN_INTERVAL"))  # Frequency (in seconds) with which to run bot
CONTINUE_DELAY = int(os.getenv("ANNOUNCER_PENALIZE_CONTINUE_DELAY"))  # Wait (in seconds) before bot runs again after a failed run


if not rpc_url:
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


async def penalize_announcer(announcer_name, rpc_client):
    log.info("Penalizing announcer %s", announcer_name)
    try:
        response = await rpc_client.upkeep_announcers_penalize(announcer_name)
    except httpx.HTTPStatusError as err:
        log.error("Failed to penalize announcer %s due to HTTPStatusError: %s", announcer_name, err)
        raise
    except httpx.ReadTimeout as err:
        log.error("Failed to penalize announcer %s due to ReadTimeout: %s", announcer_name, err)
        raise
    except ValueError as err:
        log.error("Failed to penalize announcer %s due to ValueError: %s", announcer_name, err)
        raise
    except Exception as err:
        log.error("Failed to penalize announcer %s: %s", announcer_name, err)
        raise


async def run_announcer_penalize_bot():
    log.info("Announcer penalize bot started: %s", rpc_url)
    log.info(
        "FEE_PER_COST=%s RUN_INTERVAL=%s CONTINUE_DELAY=%s",
        fee_per_cost,
        RUN_INTERVAL,
        CONTINUE_DELAY,
    )

    while True:

        await rpc_client.set_fee_per_cost()

        # show announcer
        try:
            announcers = await rpc_client.upkeep_announcers_list(penalizable=True)
        except httpx.ReadTimeout as err:
            log.exception("Failed to list penalizable announcers due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.exception("Failed to list penalizable announcers: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if len(announcers) < 1:
            log.error("No penalizable announcer found. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info("Penalizing %s penalizable announcers", len(announcers))

        penalization_tasks = []
        for announcer in announcers:
            task = asyncio.create_task(
                penalize_announcer(announcer["name"], rpc_client)
            )
            penalization_tasks.append(task)

        failed_tasks = 0
        results = await asyncio.gather(*penalization_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_tasks += 1
                log.error(f"Penalization task {i} (announcer {penalization_tasks[i]['name']}) failed: {result}")
            else:
                log.info(f"Penalization task {i} (announcer {penalization_tasks[i]['name']}) succeeded: {result}")

        if failed_tasks > 0:
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    asyncio.run(run_announcer_penalize_bot())


if __name__ == "__main__":
    main()
