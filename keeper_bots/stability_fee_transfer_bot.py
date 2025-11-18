import argparse
import asyncio
import os
import time
import random
import math
import httpx
import yaml
from dotenv import load_dotenv
import logging.config

#from chia.types.spend_bundle import SpendBundle
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


async def run_bot():
    #parser = argparse.ArgumentParser(description="Circuit reference stability fee transfer bot")
    #parser.add_argument(
    #    "--base-url",
    #    type=str,
    #    help="Base URL for the Circuit RPC API server",
    #    default="http://localhost:8000",
    #)
    #parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    #parser.add_argument(
    #    "--private-key", "-p", type=str, default=os.environ.get("PRIVATE_KEY"), help="Private key for your coins"
    #)
    #args = parser.parse_args()

    while True:

        # get vaults
        try:
            vaults = await rpc_client.upkeep_vaults_list(transferrable_stability_fees=True)
        except httpx.ReadTimeout as err:
            print("Failed to list vaults with transferrable Stability Fees due to ReadTimeout", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            print("Failed to list vaults with transferrable Stability Fees", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if not vaults:
            log.info("No vaults with transferrable Stability Fees found. Sleeping for %s seconds", RUN_INTERVAL)
            await asyncio.sleep(RUN_INTERVAL)
            continue

        vault = vaults[0]
        vault_name = vault["name"]

        await rpc_client.set_fee_per_cost()

        # transfer SFs
        try:
            response = await rpc_client.upkeep_vaults_transfer(coin_name=vault_name)
        except httpx.ReadTimeout as err:
            log.error("Failed to transfer Stability Fees from vault %s due to ReadTimeout:", vault_name, err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except httpx.HTTPStatusError as err:
            log.error("Failed to transfer Stability Fees from vault %s due to HTTPStatusError: %s", vault_name, str(err))
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to transfer Stability Fees from vault %s:", vault_name, err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Transferred Stability Fees from vault %s", vault_name)

        if len(vaults) == 1:
            await asyncio.sleep(RUN_INTERVAL)
            continue

        #final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
        #coin_name = final_bundle.additions()[0].name().hex()


def main():
    import asyncio

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
