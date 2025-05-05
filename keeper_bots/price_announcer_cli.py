import argparse
import asyncio
import os
import time
import random
import math
import httpx
import yaml
import logging.config

from chia.types.spend_bundle import SpendBundle

from circuit_cli.client import CircuitRPCClient

from okx_feed import OkxFeed

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger(__name__)

PRICE_PRECISION = 10**2
STATUTE_ANNOUNCER_MINIMUM_DEPOSIT = 31
STATUTE_ANNOUNCER_PRICE_TTL = 32
CONFIGURE_CHECK_INTERVAL = 10 * 60
PRICE_UPDATE_THRESHOLD_BPS = 100


"""
async def fetch_okx_price():
    #Fetches the latest price of a cryptocurrency from OKX API v5.
    #:return: The latest price as a float, or None if an error occurs.
    url = "https://www.okx.com/api/v5/market/ticker?instId=XCH-USDT"
    headers = {"Accept": "application/json"}

    async with httpx.AsyncClient() as http_client:
        try:
            okx_response = await http_client.get(url, headers=headers)
            okx_response.raise_for_status()  # Raises an error for bad responses
            okx_data = okx_response.json()
            return int(float(okx_data["data"][0]["last"]) * 100)
        except Exception as e:
            print(f"Error fetching crypto price: {e}")
            return None
"""

async def fetch_gateio_price():
    url = "https://api.gate.io/api2/1/ticker/xch_usdt"
    async with httpx.AsyncClient() as client:
        gateio_response = await client.get(url)
        if gateio_response.status_code == 200:
            gateio_data = gateio_response.json()
            return int(float(gateio_data.get("last")) * 100)
        else:
            raise ValueError(f"Failed to fetch price from gate.io: {gateio_response.text}")


async def run_announcer():
    parser = argparse.ArgumentParser(description="Circuit reference price announcer CLI tool")
    parser.add_argument(
        "--base-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default="http://localhost:8000",
    )
    parser.add_argument("--add-sig-data", type=str, help="Additional signature data")
    parser.add_argument(
        "--private-key", "-p", type=str, default=os.environ.get("PRIVATE_KEY"), help="Private key for your coins"
    )
    args = parser.parse_args()
    rpc_client = CircuitRPCClient(args.base_url, args.private_key)

    # Connect to OKX price feed
    sym = "XCH-USDT"
    uquote = "USD" # ultimate quote currency
    startup_window_length = 10
    window_length = 3600

    if startup_window_length > window_length:
        raise ValueError("Start-up window must not be longer than full window")

    okx_feed = OkxFeed(sym, uquote, startup_window_length, window_length, verbose=False)

    okx_feed.connect()
    okx_feed.subscribe()

    configure_last_checked_timestamp = 0
    cnt = 0 # Counter to artificially reduce oracle price over time for testing purposes
    while True:

        # show announcer
        try:
            announcers = await rpc_client.announcer_show()
        except httpx.ReadTimeout as err:
            print("Failed to get announcer list due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to get announcer list", err)
            await asyncio.sleep(10)
            continue

        assert len(announcers) > 0, "No announcer found"
        approved_announcers = [x for x in announcers if x["approved"]]
        assert len(approved_announcers) > 0, "No approved announcer found"
        assert len(approved_announcers) < 2, "More than one approved announcer found"
        announcer = approved_announcers[0]

        ### configure announcer ###
        if int(time.time()) - configure_last_checked_timestamp > CONFIGURE_CHECK_INTERVAL:

            configure_last_checked_timestamp = int(time.time())

            # get Statutes
            try:
                statutes = await rpc_client.statutes_list()
            except httpx.ReadTimeout as err:
                print("Failed to get Statutes due to ReadTimeout", err)
                break
            except ValueError as err:
                print("Failed to get Statutes due to ValueError", err)
                break
            except Exception as err:
                print("Failed to get Statutes", err)
                break

            try:
                statutes_min_deposit = int(statutes["implemented_statutes"]["ANNOUNCER_MINIMUM_DEPOSIT"]) # minimum min deposit in enacted bills and Statutes
            except KeyError as err:
                print("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to KeyError", err)
                break
            except ValueError as err:
                print("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT due to ValueError", err)
                break
            except Exception as err:
                print("Failed to get value of Statute ANNOUNCER_MINIMUM_DEPOSIT", err)
                break

            try:
                statutes_price_ttl = int(statutes["implemented_statutes"]["ANNOUNCER_PRICE_TTL"]) # minimum price TTL in enacted bills and Statutes
            except KeyError as err:
                print("Failed to get value of Statute ANNOUNCER_PRICE_TTL due to KeyError", err)
                break
            except ValueError as err:
                print("Failed to get value of Statute ANNOUNCER_PRICE_TTL due to ValueError", err)
                break
            except Exception as err:
                print("Failed to get value of Statute ANNOUNCER_PRICE_TTL", err)
                break

            min_min_deposit = statutes_min_deposit
            min_price_ttl = statutes_price_ttl

            # check for bills
            try:
                #bill_coins = rpc_client.upkeep_state(bills=True)
                bill_coins = await rpc_client.upkeep_bills_list(enacted=True)
            except httpx.ReadTimeout as err:
                print("Failed to get governance coins with bills due to ReadTimeout", err)
                break
            except ValueError as err:
                print("Failed to get governance coins with bills due to ValueError", err)
                break
            except Exception as err:
                print("Failed to get governance coins with bills", err)
                break

            for c in bill_coins:

                try:
                    if (
                            c["bill"]["statute_index"] == STATUTE_ANNOUNCER_MINIMUM_DEPOSIT
                            and c["status"]["status"] in ['IN_IMPLEMENTATION_DELAY', 'IMPLEMENTABLE']
                            and c["bill"]["value"] < min_min_deposit
                    ):
                        # update min_min_deposit
                        min_min_deposit = c["bill"]["value"]
                except KeyError as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT due to KeyError", err)
                    break
                except ValueError as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT due to ValueError", err)
                    break
                except Exception as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_MINIMUM_DEPOSIT", err)
                    break

                try:
                    if (
                            c["bill"]["statute_index"] == STATUTE_ANNOUNCER_PRICE_TTL
                            and c["status"]["status"] in ['IN_IMPLEMENTATION_DELAY', 'IMPLEMENTABLE']
                            and c["bill"]["value"] < min_price_ttl
                    ):
                        # update min_price_ttl
                        min_price_ttl = c["bill"]["value"]
                except KeyError as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_PRICE_TTL due to KeyError", err)
                    break
                except ValueError as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_PRICE_TTL due to ValueError", err)
                    break
                except Exception as err:
                    print("Failed to get info of bill for Statute ANNOUNCER_PRICE_TTL", err)
                    break

            # update new_min_deposit, and, if necessary, deposit
            new_min_deposit = None
            new_deposit = None
            req_min_deposit = max(min_min_deposit, statutes_min_deposit)
            if announcer["min_deposit"] != req_min_deposit:
                new_min_deposit = req_min_deposit
                # check if we need to increase deposit
                if announcer["deposit"] < new_min_deposit:
                    new_deposit = new_min_deposit
                    # check if we have enough XCH to increase deposit
                    try:
                        xch_balance = await rpc_client.wallet_balances()["xch"]
                    except httpx.ReadTimeout as err:
                        print("Failed to get wallet balance due to ReadTimeout", err)
                        break
                    except ValueError as err:
                        print("Failed to get wallet balance due to ValueError", err)
                        break
                    except Exception as err:
                        print("Failed to get wallet balance", err)
                        break
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
                        logger.error("Failed to configure announcer", extra=json_msg)
                        break

            # update new_price_ttl
            new_price_ttl = None
            req_price_ttl = max(min_price_ttl, statutes_price_ttl)
            if announcer["delay"] != req_price_ttl:
                new_price_ttl = req_price_ttl

            # configure announcer
            if new_price_ttl or new_min_deposit:
                print(f"Configuring announcer {announcer['name']}")
                if new_min_deposit: print(f"  MIN_DEPOSIT: {announcer['min_deposit']} -> {new_min_deposit}")
                if new_deposit: print(f"  DEPOSIT: {announcer['deposit']} -> {new_deposit}")
                if new_price_ttl: print(f"  PRICE_TTL (DELAY): {announcer['delay']} -> {new_price_ttl}")

                try:
                    response = await rpc_client.announcer_configure(
                        COIN_NAME=announcer["name"],
                        min_deposit=new_min_deposit,
                        deposit=new_deposit,
                        ttl=new_price_ttl,
                    )
                    print(f"Announcer configure tx broadcast status: {response['status']}")
                except httpx.ReadTimeout as err:
                    print("Failed to configure announcer due to ReadTimeout", err)
                    break
                except ValueError as err:
                    print("Failed to configure announcer due to ValueError", err)
                    break
                except Exception as err:
                    print("Failed to configure announcer", err)
                    break

        ### update announcer ###

        # get latest volume-weighted XCH/USD price
        try:
            price = await okx_feed.get_price()
        except Exception as err: #(TypeError, ValueError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError):
            print("Failed to fetch price, skipping.", err)
            price = announcer["price"] # if failed to get price from feed, re-publish announcer price to prevent expiry
        else:
            print(f"FETCHED VOLUME-WEIGHTED MARKET PRICE: {price}")
            if math.isnan(price):
                price = announcer["price"] # if still ramping up, re-publish announcer price to prevent expiry
            else:
                #price = int(PRICE_PRECISION * price * 0.99**cnt) # reduce oralce price by 1% per iteration #random.randint(10000, 12000)  # await fetch_okx_price()
                price = int(PRICE_PRECISION * price)
                cnt += 1

        # update announcer price

        if price < 0:
            json_msg = {
                "error_message": "XCH/USD market price is negative",
                "announcer_price": announcer["price"],
                "market_price": price,
            }
            log.error("Failed to update announcer price", extra=json_msg)
        elif price == 0:
            json_msg = {
                "warning_message": "XCH/USD market price is 0",
                "announcer_price": announcer["price"],
                "market_price": price,
            }
            log.warning("XCH/USD market price is 0")
        elif abs(announcer["price"] - price)/price - 1 < PRICE_UPDATE_THRESHOLD_BPS/10000.0:
            json_msg = {
                "info_message": "Price update threshold not reached",
                "announcer_price": announcer["price"],
                "market_price": price,
                "PRICE_UPDATE_THRESHOLD_BPS": PRICE_UPDATE_THRESHOLD_BPS,
            }
            log.info("Announcer price update skipped", extra=json_msg)
            continue

        print(f"Updating announcer {announcer['name']}. Setting price to {price}")
        try:
            response = await rpc_client.announcer_update(price, COIN_NAME=announcer["name"])
            print(f"Announcer update tx broadcast status: {response['status']}")
        except httpx.ReadTimeout as err:
            print("Failed to update announcer due to ReadTimeout", err)
            await asyncio.sleep(60)
            continue
        except ValueError as err:
            print("Failed to update announcer due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to update announcer", err)
            await asyncio.sleep(10)
            continue

        #print("Got back data", data)

        final_bundle: SpendBundle = SpendBundle.from_json_dict(response["bundle"])
        coin_name = final_bundle.additions()[0].name().hex()

        # wait for transaction to be confirmed
        try:
            print("Waiting for confirmation...")
            await rpc_client.wait_for_confirmation(final_bundle)
        except ValueError as err:
            print("Announcer update transaction failed to confirm")
            await asyncio.sleep(10)
            continue

        print("Confirmed")
        print("New announcer coin name:", coin_name)
        print("All new coins:", [coin.name().hex() for coin in final_bundle.additions()])

        # Try to update oracle
        await rpc_client.upkeep_rpc_sync()
        print("Updating oracle")
        try:
            data = await rpc_client.oracle_update()
        except httpx.ReadTimeout as err:
            print("Failed to update Oracle due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except ValueError as err:
            print("Failed to update Oracle due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to update Oracle", err)
            await asyncio.sleep(10)
            continue

        # Update statutes price if Oracle update was successful
        try:
            data = await rpc_client.statutes_update_price()
        except httpx.ReadTimeout as err:
            print("Failed to update Statutes Price due to ReadTimeout", err)
            await asyncio.sleep(10)
            continue
        except ValueError as err:
            print("Failed to update Statutes Price due to ValueError", err)
            await asyncio.sleep(10)
            continue
        except Exception as err:
            print("Failed to update Statutes Price", err)
            await asyncio.sleep(10)
            continue

        print("Updated Statutes Price. Result: ", data)
        print("Sleeping for 60 seconds")
        await asyncio.sleep(60) # 15 * 60)


def main():
    import asyncio

    asyncio.run(run_announcer())


if __name__ == "__main__":
    main()
