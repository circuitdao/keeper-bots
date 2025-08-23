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

MOJOS_PER_XCH = 1_000_000_000_000

STATUTE_ANNOUNCER_MINIMUM_DEPOSIT = 34
STATUTE_ANNOUNCER_MAXIMUM_VALUE_TTL = 35

#FEE_BUFFER_REFILL_THRESHOLD = 0.2 # fee buffer gets re-filled if buffer balance drops below this ratio
#RUN_INTERVAL = 1 * 20
#CONTINUE_DELAY = 10

async def run_announcer():
    parser = argparse.ArgumentParser(description="Circuit reference announcer bot")
    parser.add_argument(
        "--rpc-url", type=str,
        default=os.environ.get('RPC_URL', 'http://localhost:8000'),
        help="Base URL for the Circuit RPC API server",
    )
    parser.add_argument(
        "--add-sig-data", type=str,
        default=os.environ.get("ADD_SIG_DATA", ""),
        help="Additional signature data"
    )
    parser.add_argument(
        "--private-key", "-p", type=str,
        default=os.environ.get("PRIVATE_KEY", ""),
        help="Private key for your coins",
    )
    parser.add_argument(
        '--fee-per-cost', '-fpc', type=int,
        default=int(os.environ.get("FEE_PER_COST", 0)),
        help='Add transaction fee, set as fee per cost.'
    )
    parser.add_argument(
        "--run-interval", type=int,
        default=int(os.environ.get("ANNOUNCER_CONFIGURE_RUN_INTERVAL", 60)),
        help="Frequency (in seconds) with which to run bot"
    )
    parser.add_argument(
        "--continue-delay", type=int,
        default=int(os.environ.get("ANNOUNCER_CONFIGURE_CONTINUE_DELAY", 10)),
        help="Wait (in seconds) before bot runs again after a failed run"
    )
    parser.add_argument(
        "--fee-buffer", type=float,
        default=float(os.environ.get("ANNOUNCER_CONFIGURE_FEE_BUFFER", 1.0)),
        help="Amount of XCH to add to announcer min deposit to be used for tx fees (default: 1 XCH)"
    )
    parser.add_argument(
        "--fee-buffer-refill-threshold", type=float,
        default=float(os.environ.get("ANNOUNCER_CONFIGURE_FEE_BUFFER_REFILL_THRESHOLD", 0.2)),
        help="Ratio below which fee buffer gets refilled"
    )

    args = parser.parse_args()
    fee_buffer = int(args.fee_buffer * MOJOS_PER_XCH)

    if not args.private_key:
        raise ValueError("No private key provided")

    log.info("Announcer configure bot started: %s %s", args.rpc_url, len(args.private_key))
    log.info(
        "fee_per_cost=%s run_interval=%s continue_delay=%s fee_buffer=%s fee_buffer_refill_threshold=%s",
        args.fee_per_cost, args.run_interval, args.continue_delay, args.fee_buffer, args.fee_buffer_refill_threshold,
    )

    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key, args.add_sig_data, args.fee_per_cost)

    while True:

        # load env variables
        load_dotenv()

        RUN_INTERVAL = int(os.getenv("ANNOUNCER_CONFIGURE_RUN_INTERVAL"))
        CONTINUE_DELAY = int(os.getenv("ANNOUNCER_CONFIGURE_CONTINUE_DELAY"))
        fee_buffer = int(os.getenv("ANNOUNCER_CONFIGURE_FEE_BUFFER"))
        fee_buffer_refill_threshold = int(os.getenv("ANNOUNCER_CONFIGURE_FEE_BUFFER_REFILL_THRESHOLD"))

        # show announcer
        try:
            announcers = await rpc_client.announcer_show()
        except httpx.ReadTimeout as err:
            log.exception("Failed to show announcer due to ReadTimeout: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except Exception as err:
            log.exception("Failed to show announcer: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue

        # print(f"{announcers=}")
        if len(announcers) < 1:
            log.error("No announcer found. Sleeping for %s seconds", args.continue_delay)
            await asyncio.sleep(args.continue_delay)
            continue
        approved_announcers = [x for x in announcers if x["approved"]]
        if len(approved_announcers) < 1:
            log.warning("No approved announcer found. Sleeping for %s seconds", args.continue_delay)
            await asyncio.sleep(args.continue_delay)
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
            await asyncio.sleep(args.continue_delay)
            continue
        except ValueError as err:
            log.error("Failed to get Statutes due to ValueError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except Exception as err:
            log.error("Failed to get Statutes: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue

        try:
            statutes_min_deposit = int(statutes["implemented_statutes"]["ANNOUNCER_MINIMUM_DEPOSIT"])
        except KeyError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to KeyError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to ValueError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue

        try:
            statutes_price_ttl = int(statutes["implemented_statutes"]["ANNOUNCER_MAXIMUM_VALUE_TTL"])
        except KeyError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to KeyError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to ValueError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue

        max_min_deposit = statutes_min_deposit # maximum min deposit in enacted bills and Statutes
        min_price_ttl = statutes_price_ttl # minimum max price TTL in enacted bills and Statutes

        # check for bills
        try:
            # bill_coins = rpc_client.upkeep_state(bills=True)
            bill_coins = await rpc_client.upkeep_bills_list(enacted=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get governance coins with bills due to ReadTimeout: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except ValueError as err:
            log.error("Failed to get governance coins with bills due to ValueError: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue
        except Exception as err:
            log.error("Failed to get governance coins with bills: %s", err)
            await asyncio.sleep(args.continue_delay)
            continue

        log.info("Found %s enacted bills (incl lapsed ones)", len(bill_coins))

        error = False
        for c in bill_coins:

            try:
                if c["bill"]["statute_index"] == STATUTE_ANNOUNCER_MINIMUM_DEPOSIT:
                    log.info("Found bill to change announcer MIN_DEPOSIT to %s. Status: %s", c['bill']['value'],
                             c['status']['status'])
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
                if c["bill"]["statute_index"] == STATUTE_ANNOUNCER_MAXIMUM_VALUE_TTL:
                    log.info(f"Found bill to change announcer VALUE_TTL to %s. Status: %s", c['bill']['value'],
                             c['status']['status'])
                    if (
                            c["status"]["status"] in ['IN_IMPLEMENTATION_DELAY', 'IMPLEMENTABLE']
                            and c["bill"]["value"] < min_price_ttl
                    ):
                        # update min_price_ttl
                        min_price_ttl = c["bill"]["value"]
            except KeyError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to KeyError: %s", err)
                error = True
                break
            except ValueError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to ValueError: %s", err)
                error = True
                break
            except Exception as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL: %s", err)
                error = True
                break

        if error:
            log.error("Error processing governance coins with non-empty bill. Sleeping for %s seconds", args.continue_delay)
            await asyncio.sleep(args.continue_delay)
            continue

        # update new_min_deposit, and, if necessary, deposit
        new_min_deposit = None
        new_deposit = None
        req_min_deposit = max(max_min_deposit, statutes_min_deposit)
        min_acceptable_deposit = req_min_deposit + int(fee_buffer * args.fee_buffer_refill_threshold if args.fee_per_cost else 0)
        if announcer["min_deposit"] != req_min_deposit or announcer['deposit'] < min_acceptable_deposit:
            new_min_deposit = req_min_deposit
            # check if we need to increase deposit
            if announcer["deposit"] < min_acceptable_deposit:
                new_deposit = new_min_deposit + fee_buffer
                log.info("Increasing announcer deposit: %s -> %s", announcer["deposit"], new_deposit)
                # check if we have enough XCH to increase deposit
                try:
                    xch_balance = (await rpc_client.wallet_balances())["xch"]
                except httpx.ReadTimeout as err:
                    log.error("Failed to get wallet balance due to ReadTimeout: %s", err)
                    await asyncio.sleep(args.continue_delay)
                    continue
                except ValueError as err:
                    log.error("Failed to get wallet balance due to ValueError %s", err)
                    await asyncio.sleep(args.continue_delay)
                    continue
                except Exception as err:
                    log.error("Failed to get wallet balance %s", err)
                    await asyncio.sleep(args.continue_delay)
                    continue
                req_xch_balance = new_deposit - announcer["deposit"]
                if xch_balance < req_xch_balance:  # TODO: take tx fees into account
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
                    await asyncio.sleep(args.continue_delay)
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
                await asyncio.sleep(args.continue_delay)
                continue
            except ValueError as err:
                log.error("Failed to configure announcer due to ValueError: %s", err)
                await asyncio.sleep(args.continue_delay)
                continue
            except Exception as err:
                log.error("Failed to configure announcer: %s", err)
                await asyncio.sleep(args.continue_delay)
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
                await asyncio.sleep(args.continue_delay)
                continue
            except Exception as err:
                log.error("Failed to show new announcer: %s", err)
                await asyncio.sleep(args.continue_delay)
                continue

            latest_announcers = [a for a in announcers if a["launcher_id"] == announcer["launcher_id"]]
            if not latest_announcers:
                log.error(f"New announcer not found. Sleeping for {args.continue_delay} seconds")
                asyncio.sleep(args.continue_delay)
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
        log.info("Sleeping for %s seconds", args.run_interval)
        await asyncio.sleep(args.run_interval)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
