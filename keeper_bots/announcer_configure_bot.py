import argparse
import asyncio
import os
import time
import random
import math
import httpx
import json
import yaml
import logging.config
from datetime import datetime

from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("announcer_configure_bot")

STATUTE_ANNOUNCER_MINIMUM_DEPOSIT = 31
STATUTE_ANNOUNCER_VALUE_TTL = 32

RUN_INTERVAL = 1 * 20
CONTINUE_DELAY = 10

async def run_announcer():
    parser = argparse.ArgumentParser(description="Circuit reference announcer bot")
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default=os.environ.get("RPC_URL", 'http://localhost:8000'),
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument(
        "--private-key", "-p",
        type=str,
        help="Private key for your coins",
        default=os.environ.get("PRIVATE_KEY"),
    )
    args = parser.parse_args()
    log.info("Using RPC URL: %s", args.rpc_url)
    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    log.info("Announcer configure bot started")

    while True:

        # show announcer
        try:
            announcers = await rpc_client.announcer_show()
        except httpx.ReadTimeout as err:
            log.exception("Failed to show announcer due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.exception("Failed to show announcer: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        #print(f"{announcers=}")
        if len(announcers) < 1:
            log.error("No announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        approved_announcers = [x for x in announcers if x["approved"]]
        if len(approved_announcers) < 1:
            log.warning("No approved announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        if len(approved_announcers) > 1:
            log.error("More than one approved announcer found")
        announcer = approved_announcers[0]

        log.info("Found an approved announcer. Name: %s  LauncherID: %s", announcer['name'], announcer['launcher_id'])

        # get Statutes
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
            statutes_min_deposit = int(statutes["implemented_statutes"]["ANNOUNCER_MINIMUM_DEPOSIT"]) # minimum min deposit in enacted bills and Statutes
        except KeyError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to KeyError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        try:
            statutes_price_ttl = int(statutes["implemented_statutes"]["ANNOUNCER_VALUE_TTL"]) # minimum price TTL in enacted bills and Statutes
        except KeyError as err:
            log.error("Failed to get value of Statute ANNOUNCER_VALUE_TTL due to KeyError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute ANNOUNCER_VALUE_TTL due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute ANNOUNCER_VALUE_TTL: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue


        max_min_deposit = statutes_min_deposit
        min_price_ttl = statutes_price_ttl

        # check for bills
        try:
            #bill_coins = rpc_client.upkeep_state(bills=True)
            bill_coins = await rpc_client.upkeep_bills_list(enacted=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get governance coins with bills due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get governance coins with bills due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get governance coins with bills: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        log.info("Found %s enacted bills (incl lapsed ones)", len(bill_coins))

        error = False
        for c in bill_coins:

            try:
                if c["bill"]["statute_index"] == STATUTE_ANNOUNCER_MINIMUM_DEPOSIT:
                    log.info("Found bill to change announcer MIN_DEPOSIT to %s. Status: %s", c['bill']['value'], c['status']['status'])
                    if (
                        c["status"]["status"] in ['IN_IMPLEMENTATION_DELAY', 'IMPLEMENTABLE']
                        and c["bill"]["value"] > max_min_deposit
                    ):
                        # update max_min_deposit
                        max_min_deposit = c["bill"]["value"]
            except KeyError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT due to KeyError: %s", err)
                error = True
                break
            except ValueError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT due to ValueError: %s", err)
                error = True
                break
            except Exception as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT: %s", err)
                error = True
                break

            try:
                if c["bill"]["statute_index"] == STATUTE_ANNOUNCER_VALUE_TTL:
                    log.info(f"Found bill to change announcer VALUE_TTL to %s. Status: %s", c['bill']['value'], c['status']['status'])
                    if (
                            c["status"]["status"] in ['IN_IMPLEMENTATION_DELAY', 'IMPLEMENTABLE']
                            and c["bill"]["value"] < min_price_ttl
                    ):
                        # update min_price_ttl
                        min_price_ttl = c["bill"]["value"]
            except KeyError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_VALUE_TTL due to KeyError: %s", err)
                error = True
                break
            except ValueError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_VALUE_TTL due to ValueError: %s", err)
                error = True
                break
            except Exception as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_VALUE_TTL: %s", err)
                error = True
                break

        if error:
            log.error("Error processing governance coins with non-empty bill. Sleeping for %s seconds", CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        # update new_min_deposit, and, if necessary, deposit
        new_min_deposit = None
        new_deposit = None
        req_min_deposit = max(max_min_deposit, statutes_min_deposit)
        if announcer["min_deposit"] != req_min_deposit:
            new_min_deposit = req_min_deposit
            # check if we need to increase deposit
            if announcer["deposit"] < new_min_deposit:
                log.info("Must increase announcer deposit: %s -> %s", announcer["deposit"], new_min_deposit)
                new_deposit = new_min_deposit
                # check if we have enough XCH to increase deposit
                try:
                    xch_balance = (await rpc_client.wallet_balances())["xch"]
                except httpx.ReadTimeout as err:
                    log.error("Failed to get wallet balance due to ReadTimeout: %s", err)
                    await asyncio.sleep(CONTINUE_DELAY)
                    continue
                except ValueError as err:
                    log.error("Failed to get wallet balance due to ValueError %s", err)
                    await asyncio.sleep(CONTINUE_DELAY)
                    continue
                except Exception as err:
                    log.error("Failed to get wallet balance %s", err)
                    await asyncio.sleep(CONTINUE_DELAY)
                    continue
                req_xch_balance = req_min_deposit - announcer["deposit"]
                if xch_balance < req_xch_balance: # TODO: take tx fees into account
                    json_msg = {
                        "error_message": "Insufficient XCH balance to increase MIN_DEPOSIT on announcer",
                        "announcer_launcher_id": announcer["launcher_id"],
                        "announcer_name": announcer["name"],
                        "DEPOSIT": announcer["deposit"],
                        "desired_DEPOSIT": new_deposit,
                        "MIN_DEPOSIT": announcer["min_deposit"],
                        "desired_MIN_DEPOSIT": new_min_deposit,
                        "xch_balance": xch_balance,
                        "required_xch_balance": req_xch_balance,
                        "required_xch_balance_delta": req_xch_balance - xch_balance,
                    }
                    log.error("Failed to configure announcer", extra=json_msg)
                    await asyncio.sleep(CONTINUE_DELAY)
                    continue

        # update new_price_ttl
        new_price_ttl = None
        req_price_ttl = min(min_price_ttl, statutes_price_ttl)
        if announcer["price_ttl"] != req_price_ttl:
            new_price_ttl = req_price_ttl

        # configure announcer
        if new_price_ttl or new_min_deposit:
            log.info(
                (f"Configuring announcer {announcer['name']}.")
                + (f"  MIN_DEPOSIT: {announcer['min_deposit']} -> {new_min_deposit}" if new_min_deposit is not None else "")
                + (f"  DEPOSIT: {announcer['deposit']} -> {new_deposit}" if new_deposit is not None else "")
                + (f"  VALUE_TTL: {announcer['price_ttl']} -> {new_price_ttl}" if new_price_ttl is not None else "")
            )
            try:
                response = await rpc_client.announcer_configure(
                    COIN_NAME=announcer["name"],
                    min_deposit=new_min_deposit,
                    deposit=new_deposit,
                    ttl=new_price_ttl,
                    units=True,
                )
            except httpx.ReadTimeout as err:
                log.error("Failed to configure announcer due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to configure announcer due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to configure announcer: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
            coin_name = final_bundle.additions()[0].name().hex()
            log.info("New announcer coin: %s", coin_name)
            log.info("All new coins: %s", [coin.name().hex() for coin in final_bundle.additions()])

            # log state of configured announcer
            try:
                announcers = await rpc_client.announcer_show()
            except httpx.ReadTimeout as err:
                log.error("Failed to show new announcer due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to show new announcer: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

            latest_announcers = [a for a in announcers if a["launcher_id"] == announcer["launcher_id"]]
            if not latest_announcers:
                log.error(f"New announcer not found. Sleeping for {CONTINUE_DELAY} seconds")
                asyncio.sleep(CONTINUE_DELAY)
                continue

            new_announcer = latest_announcers[0]
            log.info(
                "New announcer state. INNER_PUZZLE_HASH: %s  APPROVED: %s  MIN_DEPOSIT: %s  DEPOSIT: %s  PRICE: %s  PRICE_TTL: %s",
                new_announcer["inner_puzzle_hash"],
                new_announcer["approved"],
                new_announcer["min_deposit"],
                new_announcer["deposit"],
                new_announcer["price"],
                new_announcer["price_ttl"],
            )
        else:
            log.info(
                "Leaving announcer configuration unchanged. MIN_DEPOSIT=%s  VALUE_TTL=%s",
                announcer['min_deposit'],
                announcer['price_ttl']
            )

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
