##
## Bot recovers bad debt from CircuitDAO collateral vaults
##
## The bot:
## 1) monitors protocol state for vaults in bad debt
## 2) recovers bad debt

import os
import httpx
import asyncio
import yaml
import logging
from dotenv import load_dotenv

#from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_hash_for_synthetic_public_key

from circuit_cli.client import CircuitRPCClient

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("bad_debt_recovery_bot")

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE, override=True)

rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST") # Fee per cost for transactions
CONTINUE_DELAY = int(os.getenv("BAD_DEBT_RECOVERY_CONTINUE_DELAY")) # Wait (in seconds) before job runs again after a failed run
RUN_INTERVAL = int(os.getenv("BAD_DEBT_RECOVERY_RUN_INTERVAL")) # Wait (in seconds) before bot runs again


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)

async def run_bad_debt_recovery_bot():

    log.info("Started bad debt recovery bot")

    while True:

        await rpc_client.set_fee_per_cost()

        try:
            state = await rpc_client.upkeep_state(vaults=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get state of vaults due to ReadTimeout: %s", str(err))
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get state of vaults due to ValueError: %s", str(err))
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get state of vaults: %s", str(err))
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        vaults_in_bad_debt = state.get("vaults_with_bad_debt", [])

        if not vaults_in_bad_debt:
            log.info("No vaults in bad debt. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info("%s vaults in bad debt. Starting bad debt recoveries", len(vaults_pending))

        num_vaults_not_recovered = 0
        for vault in vaults_in_bad_debt:
            try:
                vault_name = vault["name"]
                log.info("Starting bad debt recovery for vault %s", vault_name)
                response = await rpc_client.upkeep_vaults_recover(vault_name)
            except httpx.HTTPStatusError as err:
                num_vaults_not_recovered += 1
                log.error("Failed to recover bad debt from vault %s due to HTTPStatusError: %s", vault_name, err)
                continue
            except httpx.ReadTimeout as err:
                num_vaults_not_recovered += 1
                log.error("Failed to recover bad debt from vault %s due to ReadTimeout: %s", vault_name, str(err))
                continue
            except ValueError as err:
                num_vaults_not_recovered += 1
                log.error("Failed to recover bad debt from vault %s due to ValueError: %s", vault_name, str(err))
                continue
            except Exception as err:
                num_vaults_not_recovered += 1
                log.error("Failed to recover bad debt from vault %s: %s", vault_name, str(err))
                continue

            log.info("Bad debt recovered from vault %s", vault_name)

        if num_vaults_not_recovered > 0:
            log.info("Failed to recover bad debt from %s of %s vaults in bad debt. Sleeping for %s seconds", num_vaults_not_recovered, len(vaults_in_bad_debt), CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        # Since we may not have recovered all bad debt (depending on size of treasury coins), we run another iteration without delay
        log.info("Recovered some or all bad debt from all %s vaults in bad debt", len(vaults_in_bad_debt))


def main():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_bad_debt_recovery_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down bad debt recovery bot")
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == '__main__':
    main()
