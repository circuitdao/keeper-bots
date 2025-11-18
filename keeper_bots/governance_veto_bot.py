## Bot to veto bills
##
## The bot:
## 1) checks whether any proposals should be vetoed
## 2) if so, veto respective proposals

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

log = logging.getLogger("governance_veto_bot")


RUN_INTERVAL = 1 * 60
CONTINUE_DELAY = 10


async def run_governance_veto_bot():

    parser = argparse.ArgumentParser(description="Circuit reference veto bot")
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
            bills = await rpc_client.upkeep_bills_list(vetoable=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get vetoable bills due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get vetoable bills due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get vetoable bills: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        for bill in bills: # TODO: sort asc by vetoable_until

            if not is_bill_acceptable(bill): # TODO: define this function
                # veto bill
                log.info("Detected proposal of unacceptable bill. Proposal coin ID: %s. Bill: %s", bill["name"], bill["bill"])
                governance_coins = await rpc_client.wallet_coins(type="all") # TODO: error handling
                # if possible, we veto with a coin not in proposal mode
                suitable_coins = [c for c in governance_coins if c["amount"] > bill["amount"]]
                if not suitable_coins:
                    log.error(
                        "No large enough governance coin available to veto unacceptable bill %s (%s <= %s)",
                        bill["name"], max([c["amount"] for c in governance_coins]), bill["amount"]
                    )
                    continue
                    # TODO: check if we can create a large enough governance coin from plain CRT and non-proposal governance coins
                suitable_non_proposal_coins = [c for c in suitable_coins if c["bill_hash"] is None]
                if suitable_non_proposal_coins:
                    vetoing_coin = suitable_non_proposal_coins[0]
                else:
                    suitable_proposal_coins = [c for c in suitable_coins if c["bill_hash"] is not None]
                    vetoing_coin = suitable_proposal_coins[0]
                log.info(
                    "Vetoing proposal coin %s (amount: %s) with vetoing coin %s (amount: %s)",
                    bill["name"], bill["amount"], vetoing_coin["name"], vetoing_coin["amount"]
                )
                try:
                    await rpc_client.upkeep_bills_veto(target_coin_name=bill["name"], vetoing_coin_name=vetoing_coin["name"])
                except httpx.ReadTimeout as err:
                    log.error("Failed to veto bill %s due to ReadTimeout: %s", bill["name"], err)
                    continue
                except ValueError as err:
                    log.error("Failed to veto bill due to ValueError: %s", bill["name"], err)
                    continue
                except Exception as err:
                    log.error("Failed to veto bill: %s", bill["name"], err)
                    continue

                log.info("Successfully vetoed bill %s", bill["name"])

if __name__ == '__main__':

    asyncio.run(run_governance_veto_bot())

