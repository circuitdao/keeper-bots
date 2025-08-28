##
## Bot to start and settle CircuitDAO recharge auctions
##
## The bot:
## 1) retrieves recharge auction coin
## 2) settles any settlable recharge auctions
## 3) checks whether treasury is below min
## 4) if so, starts recharge auction (if there is a
##     recharge auction coin in stand-by mode)

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

log = logging.getLogger("recharge_start_settle_bot")


RUN_INTERVAL = 1 * 60
CONTINUE_DELAY = 10


async def run_recharge_start_settle_bot():

    parser = argparse.ArgumentParser(description="Circuit reference Recharge auction start and settle bot")
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
    parser.add_argument(
        "--settle-all",
        action="store_true",
        type=bool,
        default=True,
        help="Settle all recharge auctions, not just the ones we have won"
    )

    args = parser.parse_args()

    if not args.private_key:
        raise ValueError("No private key provided")

    rpc_client = CircuitRPCClient(args.rpc_url, args.private_key)

    while True:

        # get recharge auction coins
        try:
            recharge_coins = await rpc_client.upkeep_recharge_list()
        except httpx.ReadTimeout as err:
            log.error("Failed to list Recharge Auction coins due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to list Recharge Auction coins due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to list Recharge Auction coins: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue


        ### settle recharge auctions ###
        my_puzzle_hashes = []
        for k in rpc_client.synthetic_public_keys:
            my_puzzle_hashes.append(puzzle_for_synthetic_public_key(pub_key).get_tree_hash())

        for c in recharge_coins:

            if c["is_expired"]:

                # if desired, only settle if we are the winner
                if not args.settle_all and not bytes32.fromhex(c["last_bid"]["target_puzzle_hash"]) in my_puzzle_hashes:
                    log.info("Skipping settlement of expired Recharge Auction %s. Not won by us", c["name"])
                    continue

                log.info("Settling Recharge Auction %s", c["name"])

                # settle the auction
                try:
                    await rpc_client.upkeep_recharge_settle(c["name"])
                except httpx.ReadTimeout as err:
                    log.error("Failed to settle Recharge Auction due to ReadTimeout: %s", err)
                    continue
                except ValueError as err:
                    log.error("Failed to settle Recharge Auction due to ValueError: %s", err)
                    continue
                except Exception as err:
                    log.error("Failed to settle Recharge Auction: %s", err)
                    continue

                log.info("Settled Recharge Auction %s", c["name"])

        ### start recharge auctions ###

        # get info on treasury
        try:
            treasury = await rpc_client.upkeep_treasury_show()
        except httpx.ReadTimeout as err:
            log.error("Failed to show Treasury due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to show Treasury due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to show Treasury: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if treasury["can_start_recharge_auction"]:

            # start a recharge auction
            started = False
            for c in recharge_coins:

                if c["status"] == "STANDBY":

                    log.info("Starting Recharge Auction %s", c["name"])

                    try:
                        await rpc_client.upkeep_recharge_start(COIN_NAME=c.name)
                    except httpx.ReadTimeout as err:
                        log.error("Failed to start Recharge Auction due to ReadTimeout: %s", err)
                        continue
                    except ValueError as err:
                        log.error("Failed to start Recharge Auction due to ValueError: %s", err)
                        continue
                    except Exception as err:
                        log.error("Failed to start Recharge Auction: %s", err)
                        continue
                    else:
                        log.info("Started Recharge Auction %s", c["name"])
                        started = True
                        break

            if not started:
                log.error("Failed to start Recharge Auction on any of the %s Recharge Auction coins on stand-by", len([c for c in recharge_coins if c["statuts"] == "STANDBY"]))
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            else:
                log.info("Started Recharge Auction %s", c["name"])

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


if __name__ == '__main__':

    asyncio.run(run_recharge_start_settle_bot())

