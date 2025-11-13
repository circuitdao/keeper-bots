## Configures an announcer to prevent penalization
##
## If only one announcer is found, the bot configures it whether approved or not.
## If multiple announcers are found, the bot selects the first approved announcer that got returned
## or, if no approved announcers were found, the first non-approved announcer returned.
##
## The bot monitors
## - Statutes
## - enacted bills
## - the announcer itself
## to configure parameters in a timely manner to prevent the announcer from becoming penalizable.

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

log = logging.getLogger("announcer_configure_bot")


MOJOS_PER_XCH = 1_000_000_000_000
STATUTE_ANNOUNCER_MINIMUM_DEPOSIT = 34
STATUTE_ANNOUNCER_MAXIMUM_VALUE_TTL = 35

load_dotenv(override=True)


rpc_url = str(os.getenv("RPC_URL"))  # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY"))  # Private master key that controls announcer
add_sig_data = os.getenv("ADD_SIG_DATA")  # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST")  # Fee per cost for transactions
RUN_INTERVAL = int(os.getenv("ANNOUNCER_CONFIGURE_RUN_INTERVAL"))  # Frequency (in seconds) with which to run bot
CONTINUE_DELAY = int(
    os.getenv("ANNOUNCER_CONFIGURE_CONTINUE_DELAY")
)  # Wait (in seconds) before bot runs again after a failed run
configure_unapproved_announcer = os.getenv("ANNOUNCER_CONFIGURE_CONFIGURE_UNAPPROVED_ANNOUNCER")
if configure_unapproved_announcer == "true":
    CONFIGURE_UNAPPROVED_ANNOUNCER = True
elif configure_unapproved_announcer == "false":
    CONFIGURE_UNAPPROVED_ANNOUNCER = False
else:
    raise ValueError("ANNOUNCER_CONFIGURE_CONFIGURE_UNAPPROVED_ANNOUNCER must be set to either 'true' or 'false'")
DEPOSIT_BUFFER = int(
    float(os.getenv("ANNOUNCER_CONFIGURE_DEPOSIT_BUFFER")) * MOJOS_PER_XCH
)  # additional deposit (in XCH) on top of MIN_DEPOSIT to keep in announcer
DEPOSIT_BUFFER_REFILL_THRESHOLD = float(
    os.getenv("ANNOUNCER_CONFIGURE_DEPOSIT_BUFFER_REFILL_THRESHOLD")
)  # threshold (eg 0.2 = 20%) below which we fully refill deposit buffer

if not rpc_url:
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    raise ValueError("No master private key found")

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


async def run_announcer():
    log.info("Announcer configure bot started: %s", rpc_url)
    log.info(
        "FEE_PER_COST=%s RUN_INTERVAL=%s CONTINUE_DELAY=%s ANNOUNCER_CONFIGURE_CONFIGURE_UNAPPROVED_ANNOUNCER=%s DEPOSIT_BUFFER=%s DEPOSIT_BUFFER_REFILL_THRESHOLD=%s",
        fee_per_cost,
        RUN_INTERVAL,
        CONTINUE_DELAY,
        CONFIGURE_UNAPPROVED_ANNOUNCER,
        DEPOSIT_BUFFER,
        DEPOSIT_BUFFER_REFILL_THRESHOLD,
    )

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

        if len(announcers) < 1:
            log.error("No announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        if len(announcers) > 1:
            approved_announcers = [x for x in announcers if x["approved"]]
            log.warning("%s announcers found, %s of them approved", len(announcers), len(approved_announcers))
            if approved_announcers:
                announcer = approved_announcers[0]
            elif not CONFIGURE_UNAPPROVED_ANNOUNCER:
                log.error("No approved announcer found. Sleeping for %s", RUN_INTERVAL)
                await asyncio.sleep(RUN_INTERVAL)
                continue
            else:
                announcer = announcers[0]
            log.info("Selected an announcer. Name: %s. Launcher ID: %s. Approval status: %s", announcer["name"], announcer["launcher_id"], announcer["approved"])
        else:
            announcer = announcers[0]
            log.info("Found 1 announcer. Name: %s. LauncherID: %s. Approval status: %s", announcer["name"], announcer["launcher_id"], announcer["approved"])

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
            statutes_min_deposit = int(statutes["implemented_statutes"]["ANNOUNCER_MINIMUM_DEPOSIT"])
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
            statutes_price_ttl = int(statutes["implemented_statutes"]["ANNOUNCER_MAXIMUM_VALUE_TTL"])
        except KeyError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to KeyError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get value of Statute ANNOUNCER_MAXIMUM_VALUE_TTL: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        max_min_deposit = statutes_min_deposit  # maximum min deposit in enacted bills and Statutes
        min_price_ttl = statutes_price_ttl  # minimum max price TTL in enacted bills and Statutes

        # check for enacted bills (ie those that can no longer be vetoed)
        try:
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
                    log.info(
                        "Found bill to change announcer MIN_DEPOSIT to %s. Status: %s",
                        c["bill"]["value"],
                        c["status"]["status"],
                    )
                    if (
                        c["status"]["status"] in ["IN_IMPLEMENTATION_DELAY", "IMPLEMENTABLE"]
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
                    log.info(
                        "Found bill to change announcer VALUE_TTL to %s. Status: %s",
                        c["bill"]["value"],
                        c["status"]["status"],
                    )
                    if (
                        c["status"]["status"] in ["IN_IMPLEMENTATION_DELAY", "IMPLEMENTABLE"]
                        and c["bill"]["value"] < min_price_ttl
                    ):
                        # update min_price_ttl
                        min_price_ttl = c["bill"]["value"]
            except KeyError as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to KeyError: %s", err)
                error = True
                break
            except ValueError as err:
                log.error(
                    "Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL due to ValueError: %s", err
                )
                error = True
                break
            except Exception as err:
                log.error("Failed to get info of bill for Statute ANNOUNCER_MAXIMUM_VALUE_TTL: %s", err)
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
        min_acceptable_deposit = req_min_deposit  + int(DEPOSIT_BUFFER * DEPOSIT_BUFFER_REFILL_THRESHOLD)
        max_acceptable_deposit = req_min_deposit + DEPOSIT_BUFFER
        if (
                announcer["min_deposit"] != req_min_deposit
                or announcer["deposit"] < min_acceptable_deposit
                or announcer["deposit"] > max_acceptable_deposit
        ):
            new_min_deposit = req_min_deposit
            # check if we need to increase deposit
            if announcer["deposit"] < min_acceptable_deposit or announcer["deposit"] > max_acceptable_deposit:
                new_deposit = new_min_deposit + DEPOSIT_BUFFER
                if new_deposit >= announcer["deposit"]:
                    log.info("Increasing announcer deposit: %s -> %s", announcer["deposit"], new_deposit)
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
                        await asyncio.sleep(CONTINUE_DELAY)
                        continue
                else:
                    log.info("Reducing announcer deposit: %s -> %s", announcer["deposit"], new_deposit)

        # update new_price_ttl
        new_price_ttl = None
        req_price_ttl = min(min_price_ttl, statutes_price_ttl)
        if announcer["price_ttl"] != req_price_ttl:
            new_price_ttl = req_price_ttl

        # configure announcer
        if new_price_ttl or new_min_deposit:
            log.info(
                ("Configuring announcer {announcer['name']}.")
                + (
                    f"  MIN_DEPOSIT: {announcer['min_deposit']} -> {new_min_deposit}"
                    if new_min_deposit is not None
                    else ""
                )
                + (f"  DEPOSIT: {announcer['deposit']} -> {new_deposit}" if new_deposit is not None else "")
                + (f"  VALUE_TTL: {announcer['price_ttl']} -> {new_price_ttl}" if new_price_ttl is not None else "")
            )
            await rpc_client.set_fee_per_cost()
            try:
                response = await rpc_client.announcer_configure(
                    coin_name=announcer["name"],
                    min_deposit=new_min_deposit,
                    deposit=new_deposit,
                    ttl=new_price_ttl,
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
            coin_name = final_bundle.additions()[-1].name().hex()
            log.info("New announcer coin: %s", coin_name)
            log.info("All new coins: %s", [coin.name().hex() for coin in final_bundle.additions()])

            # log state of configured announcer
            try:
                announcers = await rpc_client.announcer_show()
            except httpx.ReadTimeout as err:
                log.error("Failed to show new announcer due to ReadTimeout: %s", err)
                log.info("Sleeping for %s seconds", RUN_INTERVAL)
                await asyncio.sleep(RUN_INTERVAL)
                continue
            except Exception as err:
                log.error("Failed to show new announcer: %s", err)
                log.info("Sleeping for %s seconds", RUN_INTERVAL)
                await asyncio.sleep(RUN_INTERVAL)
                continue

            latest_announcers = [a for a in announcers if a["launcher_id"] == announcer["launcher_id"]]
            if not latest_announcers:
                log.error(f"New announcer not found. Sleeping for {RUN_INTERVAL} seconds")
                await asyncio.sleep(RUN_INTERVAL)
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
                announcer["min_deposit"],
                announcer["price_ttl"],
            )

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
