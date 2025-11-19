## Bot to implement bills
##
## The bot:
## 1) checks whether any of our bills can be implemented
## 2) if so, implements those bills

import os
import asyncio
import httpx
import yaml
import logging.config
from dotenv import load_dotenv


from circuit_cli.client import APIError, CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("governance_implement_bot")


async def run_governance_implement_bot():

    try:
        load_dotenv(override=True)
    except Exception as err:
        log.error("Failed to load .env: %s", str(err))

    rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
    private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
    add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
    fee_per_cost = int(os.getenv("FEE_PER_COST")) # Fee per cost for transactions
    RUN_INTERVAL = int(os.getenv("IMPLEMENT_RUN_INTERVAL")) # Frequency (in seconds) with which to run bot
    CONTINUE_DELAY = int(os.getenv("IMPLEMENT_CONTINUE_DELAY")) # Wait (in seconds) before bot runs again after a failed run
    if not rpc_url:
        raise ValueError("No URL found at which Circuit RPC server can be reached")
    if not private_key:
        raise ValueError("No master private key found")

    rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)

    while True:

        try:
            bills = await rpc_client.bills_list(implementable=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get implementable bills due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get implementable bills due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get implementable bills: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if len(bills) == 0:
            log.info(f"No implementable bills found. Sleeping for {RUN_INTERVAL} seconds")
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info(f"Found {len(bills)} implementable bills")

        # get the latest fee per cost
        await rpc_client.set_fee_per_cost()

        failed_implementations = 0
        for bill in bills:

            coin_name = bill["name"]
            log.info(
                "Implementing bill for Statute [%s] %s: %s -- %s %s %s %s. Coin ID: %s",
                bill["bill"]["statute_index"], bill["bill"]["statute_name"],
                bill["bill"]["value"], bill["bill"]["threshold_amount_to_propose"], bill["bill"]["veto_interval"],
                bill["bill"]["implementation_delay"], bill["bill"]["max_delta"], coin_name
            )
            try:
                await rpc_client.bills_implement(coin_name=coin_name)
            except APIError as err:
                failed_implementations += 1
                if "non-announce operation failed" in str(err):
                    log.error(f"Failed to implement bill {coin_name}: {str(err)}")
                    log.info("Announcing Statutes")
                    try:
                        await rpc_client.statutes_announce()
                    except Exception as err:
                        log.error(f"Failed to announce Statutes: {str(err)}")
                        log.info(f"Sleeping for {CONTINUE_DELAY} seconds")
                        await asyncio.sleep(CONTINUE_DELAY)
                        continue
                    # no need to retry implementing bill as failed_implementation has been incremented
                    continue
                else:
                    log.error("APIError implementing bill %s: %s", coin_name, err)
            except httpx.ReadTimeout as err:
                log.error("Failed to implement bill %s due to ReadTimeout: %s", coin_name, err)
                failed_implementations += 1
                continue
            except ValueError as err:
                log.error("Failed to implement bill %s due to ValueError: %s", coin_name, err)
                failed_implementations += 1
                continue
            except Exception as err:
                log.error("Failed to implement bill %s: %s", coin_name, err)
                failed_implementations += 1
                continue

            log.info("Successfully implemented bill %s", coin_name)

        if failed_implementations > 0:
            log.info(f"Failed to implement {failed_implementations} of {len(bills)} implementable bills. Sleeping for {CONTINUE_DELAY} seconds")
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info(f"Successfully implemented all {len(bills)} implementable bills. Sleeping for {RUN_INTERVAL} seconds")
        await asyncio.sleep(RUN_INTERVAL)

if __name__ == '__main__':
    asyncio.run(run_governance_implement_bot())

