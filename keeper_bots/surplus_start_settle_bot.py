## Bot to start and settle CircuitDAO surplus auctions
##
## The bot:
## 1) checks whether treasury above max threshold
## 2) if so, starts surplus auction
## 3) retrieves surplus auction coins
## 4) settles any settlable surplus auctions

import os
import asyncio
import argparse
import httpx
import yaml
import logging.config
from dotenv import load_dotenv

from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    puzzle_hash_for_synthetic_public_key,
)

from circuit_cli.client import CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("surplus_start_settle_bot")


# .env lives at the keeper-bots repo root (one level above this package dir)
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

RUN_INTERVAL = int(
    os.getenv("SURPLUS_START_SETTLE_RUN_INTERVAL", "60")
)  # Wait (in seconds) between runs
CONTINUE_DELAY = int(
    os.getenv("SURPLUS_START_SETTLE_CONTINUE_DELAY", "10")
)  # Wait (in seconds) before running again after a failed run


async def run_surplus_start_settle_bot():

    parser = argparse.ArgumentParser(description="Circuit reference Surplus Auction start and settle bot")
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Base URL for the Circuit RPC API server",
        default=os.environ.get("RPC_URL", "http://localhost:8000"),
    )
    parser.add_argument(
        "--add-sig-data",
        type=str,
        default=os.environ.get("ADD_SIG_DATA"),
        help="Additional signature data",
    )
    parser.add_argument(
        "--fee-per-cost",
        type=str,
        default=os.environ.get("FEE_PER_COST", "fast"),
        help="Fee per cost for transactions",
    )
    parser.add_argument(
        "--private-key", "-p",
        type=str,
        default=os.environ.get("PRIVATE_KEY"),
        help="Private key for your coins",
    )
    parser.add_argument(
        "--settle-all",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Settle all surplus auctions, not just the ones we have won"
    )


    args = parser.parse_args()

    if not args.private_key:
        raise ValueError("No private key provided")

    rpc_client = CircuitRPCClient(
        args.rpc_url, args.private_key, args.add_sig_data, args.fee_per_cost
    )

    while True:

        # resolve fee_per_cost (e.g. the "fast" preset) before submitting any
        # transaction; otherwise self.fee_per_cost stays None and the API rejects
        # the request with a fee_per_cost float_type error
        try:
            await rpc_client.set_fee_per_cost()
        except Exception as err:
            log.error("Failed to set fee_per_cost: %s", str(err))
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        ### start surplus auction ###

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

        can_start = treasury["can_start_surplus_auction"]
        log.info(
            "Surplus Auction can%s be started", "" if can_start else "not"
        )

        if can_start:

            # start a surplus auction
            try:
                await rpc_client.upkeep_surplus_start()
            except httpx.ReadTimeout as err:
                log.error("Failed to start Surplus Auction due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to start Surplus Auction due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to start Surplus Auction: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue

        ### settle surplus auctions ###

        # get surplus auction coins
        try:
            surplus_coins = await rpc_client.upkeep_surplus_list()
        except httpx.ReadTimeout as err:
            log.error("Failed to list Surplus Auction coins due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to list Surplus Auction coins due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to list Surplus Auction coins: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        settleable = [c for c in surplus_coins if c["can_be_settled"]]
        if settleable:
            log.info(
                "%s Surplus Auction(s) can be settled: %s",
                len(settleable),
                ", ".join(c["name"] for c in settleable),
            )
        else:
            log.info("No Surplus Auction can be settled")

        my_puzzle_hashes = {
            puzzle_hash_for_synthetic_public_key(pk).hex()
            for pk in rpc_client.synthetic_public_keys
        }

        for c in surplus_coins:

            if c["can_be_settled"]:

                # if desired, only settle auctions we have won
                if (
                    not args.settle_all
                    and c["last_bid"]["target_puzzle_hash"] not in my_puzzle_hashes
                ):
                    log.info("Skipping settlement of expired Surplus Auction %s. Not won by us", c["name"])
                    continue

                log.info("Settling Surplus Auction %s", c["name"])

                # settle the auction
                try:
                    await rpc_client.upkeep_surplus_settle(c["name"])
                except httpx.ReadTimeout as err:
                    log.error("Failed to settle Surplus Auction due to ReadTimeout: %s", err)
                    continue
                except ValueError as err:
                    log.error("Failed to settle Surplus Auction due to ValueError: %s", err)
                    continue
                except Exception as err:
                    log.error("Failed to settle Surplus Auction: %s", err)
                    continue

                log.info("Settled Surplus Auction %s", c["name"])


        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    try:
        asyncio.run(run_surplus_start_settle_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down surplus start/settle bot")


if __name__ == '__main__':
    main()

