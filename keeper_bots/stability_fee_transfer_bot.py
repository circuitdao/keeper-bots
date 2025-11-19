## Bot to transfer Stability Fees
##
## The bot:
## 1) checks whether any vaults have accrued SFs that can be transferred to Treasury
## 2) if so, transfers those SFs

import os
import asyncio
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


log = logging.getLogger("stability_fee_transfer_bot")


load_dotenv(override=True)


rpc_url = str(os.getenv("RPC_URL"))  # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY"))  # Private master key that controls announcer
add_sig_data = os.getenv("ADD_SIG_DATA")  # Additional signature data (depends on network)
fee_per_cost = int(os.getenv("FEE_PER_COST"))  # Fee per cost for transactions
RUN_INTERVAL = int(os.getenv("SF_TRANSFER_RUN_INTERVAL"))  # Frequency (in seconds) with which to run bot
CONTINUE_DELAY = int(os.getenv("SF_TRANSFER_CONTINUE_DELAY")) # Wait (in seconds) before bot runs again after a failed run

if not rpc_url:
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    raise ValueError("No master private key found")


rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


async def run_stability_fee_transfer_bot():

    try:
        load_dotenv(override=True)
    except Exception as err:
        log.error("Failed to load .env: %s", str(err))

    rpc_url = str(os.getenv("RPC_URL")) # Base URL for Circuit RPC API server
    private_key = str(os.getenv("PRIVATE_KEY")) # Private master key that controls announcer
    add_sig_data = str(os.getenv("ADD_SIG_DATA")) # Additional signature data (depends on network)
    fee_per_cost = int(os.getenv("FEE_PER_COST")) # Fee per cost for transactions
    RUN_INTERVAL = int(os.getenv("SF_TRANSFER_RUN_INTERVAL")) # Frequency (in seconds) with which to run bot
    CONTINUE_DELAY = int(os.getenv("SF_TRANSFER_CONTINUE_DELAY")) # Wait (in seconds) before bot runs again after a failed run
    if not rpc_url:
        raise ValueError("No URL found at which Circuit RPC server can be reached")
    if not private_key:
        raise ValueError("No master private key found")

    rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)

    while True:

        # get vaults
        try:
            vaults = await rpc_client.upkeep_vaults_list(transferable_stability_fees=True)
        except httpx.ReadTimeout as err:
            print("Failed to list vaults with transferable Stability Fees due to ReadTimeout", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            print("Failed to list vaults with transferable Stability Fees", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if not vaults:
            log.info("No vaults with transferable Stability Fees found. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        log.info(f"Found {len(vaults)} vaults with transferable Stability Fees")

        failed_transfers = 0
        for vault in vaults:

            vault_name = vault["name"]

            await rpc_client.set_fee_per_cost()

            # transfer SFs
            try:
                response = await rpc_client.upkeep_vaults_transfer(coin_name=vault_name)
            except httpx.ReadTimeout as err:
                log.error("Failed to transfer Stability Fees from vault %s due to ReadTimeout:", vault_name, err)
                failed_transfers += 1
                continue
            except httpx.HTTPStatusError as err:
                log.error("Failed to transfer Stability Fees from vault %s due to HTTPStatusError: %s", vault_name, str(err))
                failed_transfers += 1
                continue
            except Exception as err:
                log.error("Failed to transfer Stability Fees from vault %s:", vault_name, err)
                failed_transfers += 1
                continue

            log.info("Transferred Stability Fees from vault %s", vault_name)

        if failed_transfers > 0:
            log.info(f"Failed to transfer SFs from {failed_transfers} of {len(vaults)} vaults. Sleeping for {CONTINUE_DELAY} seconds")
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info(f"Successfully transferred SFs from all {len(vaults)} vaults that had transferable SFs. Sleeping for {RUN_INTERVAL} seconds")
        await asyncio.sleep(RUN_INTERVAL)

        #final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
        #coin_name = final_bundle.additions()[0].name().hex()


if __name__ == '__main__':
    asyncio.run(run_stability_fee_transfer_bot())

