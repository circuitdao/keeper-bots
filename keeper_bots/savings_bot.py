## Savings bot
##
## The bot runs at a pre-defined frequency to
## withdraw interest from treasury to savings vault

import os
import asyncio
import httpx
import yaml
import random
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import pytz
import logging.config

from circuit_cli.client import CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("savings_bot")


ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE, override=True)

rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST") # Fee per cost for transactions
CONTINUE_DELAY = int(os.getenv("SAVINGS_CONTINUE_DELAY")) # Wait (in seconds) before job runs again after a failed run
RUN_INTERVAL = int(os.getenv("SAVINGS_RUN_INTERVAL")) # Wait (in seconds) before job runs again after a failed interest withdrawal due to insufficiently large treasury coins
MAX_NUM_RUNS = int(os.getenv("SAVINGS_MAX_NUM_RUNS")) # Wait (in seconds) before job runs again after a failed interest withdrawal due to insufficiently large treasury coins

if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


async def run_savings_job():

    num_runs = 1

    while True:

        # get savings vault
        log.info("Getting savings vault")
        try:
            savings_vault = await rpc_client.savings_show()
        except httpx.ReadTimeout as err:
            log.error("Failed to show savings vault due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to show savings vault due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to show savings vault: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        accrued_interest = savings_vault["accrued_interest"]
        vault_name = savings_vault["name"]

        log.info("Found savings vault. Accrued interest: %s mBYC, name: %s", accrued_interest, vault_name)

        # get Statutes
        log.info("Getting Statutes")
        try:
            statutes = await rpc_client.statutes_list()
        except httpx.ReadTimeout as err:
            log.error("Failed to get Statutes due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get Statutes due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get Statutes: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue


        try:
            statutes_min_treasury_delta = int(statutes["implemented_statutes"]["TREASURY_MINIMUM_DELTA"])
        except KeyError as err:
            log.error("Failed to get value of Statute TREASURY_MINIMUM_DELTA due to KeyError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute TREASURY_MINIMUM_DELTA due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute TREASURY_MINIMUM_DELTA: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Got Statutes. Treasury Minimum Delta: %s", statutes_min_treasury_delta)

        # return if accrued interest too small to withdraw
        if accrued_interest <= statutes_min_treasury_delta:
            log.info(
                "Accrued interest does not exceed minimum treasury delta (%s <= %s). Interest withdrawal not possible",
                accrued_interest, statutes_min_treasury_delta
            )
            break


        # withdraw interest from treasury to savings vault
        log.info("Withdrawing accrued interest of %s mBYC to savings vault %s", accrued_interest, vault_name)
        try:
            response = await rpc_client.savings_withdraw(0)
        except httpx.ReadTimeout as err:
            log.error("Failed to withdraw interest to savings vault due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to withdraw interest to savings vault due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to withdraw interest to savings vault: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if not "message" in response.keys():
            log.info("Accrued interest of %s mBYC withdrawn to savings vault %s", accrued_interest, vault_name)
            break

        # no-op
        if num_runs < MAX_NUM_RUNS:
            num_runs += 1
            log.warning(
                "No treasury coin with large enough balance for full accrued interest (%s) withdrawal found. Next attempt (%s/%s) in %s seconds",
                accrued_interest, num_runs, MAX_NUM_RUNS, RUN_INTERVAL,
            )
            await asyncio.sleep(RUN_INTERVAL)
            continue
        else:
            log.error(
                "No treasury coin with large enough balance for full accrued interest (%s) withdrawal found. Max number of attempts reached",
                accrued_interest
            )
            break


def load_schedule_params() -> dict:
    TIMEZONE = os.getenv("SAVINGS_TIMEZONE", "UTC") # UTC as default timezone
    YEAR = os.getenv("SAVINGS_YEAR")
    MONTH = os.getenv("SAVINGS_MONTH")
    DAY_OF_WEEK = os.getenv("SAVINGS_DAY_OF_WEEK", "*")
    DAY = os.getenv("SAVINGS_DAY")
    HOUR = os.getenv("SAVINGS_HOUR")
    MINUTE = os.getenv("SAVINGS_MINUTE")
    SECOND = os.getenv("SAVINGS_SECOND")
    # Accepted formats for START_DATE and END_DATE:
    #   "2023-06-15T14:30:00Z"  # UTC with 'Z'
    #   "2023-06-15T14:30:00+00:00"  # UTC with offset
    START_DATE = os.getenv("SAVINGS_START_DATE", None)
    END_DATE = os.getenv("SAVINGS_END_DATE", None)
    hour_randomized = minute_randomized = second_randomized = False
    if HOUR == "random":
        hour_randomized = True
        HOUR = str(random.randint(0, 23))
    if MINUTE == "random":
        minute_randomized = True
        MINUTE = str(random.randint(0, 59))
    if SECOND == "random":
        second_randomized = True
        SECOND = str(random.randint(0, 59))
    try:
        start_date = datetime.fromisoformat(START_DATE) if START_DATE else None # results in timezone aware datetime
    except ValueError as err:
        log.error("Invalid ISO format for START_DATE: %s", str(err))
        raise
    try:
        end_date = datetime.fromisoformat(END_DATE) if END_DATE else None # results in timezone aware datetime
    except ValueError as err:
        log.error("Invalid ISO format for END_DATE: %s", str(err))
        raise
    return {
        "timezone": TIMEZONE,
        "year": YEAR,
        "month": MONTH,
        "day_of_week": DAY_OF_WEEK,
        "day": DAY,
        "hour": HOUR,
        "minute": MINUTE,
        "second": SECOND,
        "start_date": start_date,
        "end_date": end_date,
    }


async def main():

    # Load schedule
    schedule = load_schedule_params()

    # Create scheduler
    scheduler = AsyncIOScheduler()

    # Schedule savings job
    scheduler.add_job(
        run_savings_job,
        CronTrigger(**schedule),
        id="savings_job",
        name="Savings job",
    )

    # Start the scheduler
    log.info("Starting scheduler")
    log.info("Savings job schedule: %s", schedule)
    scheduler.start()

    # Keep the event loop running
    try:
        await asyncio.Event().wait()  # Wait indefinitely until interrupted
    except KeyboardInterrupt:
        log.info("Shutting down scheduler...")
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
