##
## Bot that starts CircuitDAO liquidation auctions
##
## The bot:
## 1) monitors protocol state for pending liquidations
## 2) triggers liquidation auctions

import os
import httpx
import asyncio
import yaml
import logging
from dotenv import load_dotenv

from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_hash_for_synthetic_public_key

from circuit_cli.client import CircuitRPCClient

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("liquidation_start_bot")

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_FILE, override=True)

rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST") # Fee per cost for transactions
CONTINUE_DELAY = int(os.getenv("LIQUIDATION_START_CONTINUE_DELAY")) # Wait (in seconds) before job runs again after a failed run
RUN_INTERVAL = int(os.getenv("LIQUIDATION_START_RUN_INTERVAL")) # Wait (in seconds) before bot runs again


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)

async def run_liquidation_start_bot():

    my_puzzle_hash = puzzle_hash_for_synthetic_public_key(rpc_client.synthetic_public_keys[0]).hex()

    log.info("Started liquidation start bot")

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

        vaults_pending = state.get("vaults_pending_liquidation", [])

        if not vaults_pending:
            log.info("No vaults pending liquidation. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info("%s vaults pending liquidation. Starting liquidation auctions", len(vaults_pending))

        num_vaults_not_liquidated = 0
        for vault in vaults_pending:
            try:
                vault_name = vault["name"]
                log.info("Starting liquidation auction for vault %s", vault_name)
                response = await rpc_client.upkeep_vaults_liquidate(vault_name, target_puzzle_hash=my_puzzle_hash)
            except httpx.ReadTimeout as err:
                num_vaults_not_liquidated += 1
                log.error("Failed to start liquidation auction for vault %s due to ReadTimeout: %s", vault_name, str(err))
                continue
            except ValueError as err:
                num_vaults_not_liquidated += 1
                log.error("Failed to start liquidation auction for vault %s due to ValueError: %s", vault_name, str(err))
                continue
            except Exception as err:
                num_vaults_not_liquidated += 1
                log.error("Failed to start liquidation auction for vault %s: %s", vault_name, str(err))
                continue

            log.info("Liquidation auction started for vault %s", vault_name)

        if num_vaults_not_liquidated > 0:
            log.info("Failed to start liquidation for %s of %s liquidatable vaults. Sleeping for %s seconds", num_vaults_not_liquidated, len(vaults_pending), CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Started liquidation for all %s liquidatable vaults. Sleeping for %s seconds", len(vaults_pending), RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    #asyncio.run(run_liquidation_start_bot())
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_liquidation_start_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down liquidation start bot")
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == '__main__':
    main()
