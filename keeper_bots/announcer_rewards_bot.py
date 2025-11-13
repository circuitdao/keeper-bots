## Bot that performs registry-related operaitons
##
## The bot:
## 1) retrieves approved announcer. if any, it proceeds to:
## 2) check whether announcer is registered
## 3) if not, it registers the announcer
## 4) check whether rewards can be distributed
## 5) if so, it distrubtes rewards
##
## Note that the bot will not distribute rewards if the user running it
## doesn't have an approved announcer.


import asyncio
import os
import httpx
import yaml
import logging.config
from dotenv import load_dotenv

from chia_rs import SpendBundle
from chia_rs.sized_bytes import bytes32

from circuit_cli.client import CircuitRPCClient

if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("announcer_rewards_bot")


load_dotenv(override=True)


rpc_url = str(os.getenv("RPC_URL"))  # Base URL for Circuit RPC API server
private_key = str(os.getenv("PRIVATE_KEY"))  # Private master key that controls announcer
add_sig_data = os.getenv("ADD_SIG_DATA")  # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST")  # Fee per cost for transactions
RUN_INTERVAL = int(os.getenv("ANNOUNCER_REWARDS_RUN_INTERVAL"))  # Frequency (in seconds) with which to run bot
CONTINUE_DELAY = int(os.getenv("ANNOUNCER_REWARDS_CONTINUE_DELAY"))  # Wait (in seconds) before bot runs again after a failed run
TARGET_PUZZLE_HASH = os.getenv("ANNOUNCER_REWARDS_TARGET_PUZZLE_HASH", None)

if not rpc_url:
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    raise ValueError("No master private key found")
if not TARGET_PUZZLE_HASH in [None, ""]:
    try:
        bytes32.fromhex(TARGET_PUZZLE_HASH)
    except:
        log.exception("Invalid TARGET_PUZZLE_HASH. Must be None, empty string, or convertible to type bytes32")
        raise
elif TARGET_PUZZLE_HASH == "":
    TARGET_PUZZLE_HASH = None

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)


async def run_announcer_rewards_bot():
    log.info("Announcer rewards bot started: %s", rpc_url)
    log.info(
        "FEE_PER_COST=%s RUN_INTERVAL=%s CONTINUE_DELAY=%s TARGET_PUZZLE_HASH=%s",
        fee_per_cost,
        RUN_INTERVAL,
        CONTINUE_DELAY,
        TARGET_PUZZLE_HASH,
    )

    while True:

        await rpc_client.set_fee_per_cost()

        # show announcer
        try:
            approved_announcers = await rpc_client.announcer_show(approved=True)
        except httpx.ReadTimeout as err:
            log.exception("Failed to show announcer due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.exception("Failed to show announcer: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if len(approved_announcers) < 1:
            log.error("No approved announcer found. Sleeping for %s seconds", CONTINUE_DELAY)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        announcer = approved_announcers[0]
        name = announcer["name"]
        launcher_id = announcer["launcher_id"]

        log.info("Found an approved announcer. Name: %s  LauncherID: %s", name, launcher_id)

        success = True
        # register announcer
        if not announcer["registered"]:
            log.info("Registering announcer")
            try:
                response = await rpc_client.announcer_register(coin_name=name, target_puzzle_hash=TARGET_PUZZLE_HASH)
            except httpx.ReadTimeout as err:
                log.error("Failed to register announcer %s due to ReadTimeout: %s", name, err)
                success = False
            except ValueError as err:
                log.error("Failed to register announcer %s due to ValueError: %s", name, err)
                success = False
            except Exception as err:
                log.error("Failed to register announcer %s: %s", name, err)
                success = False
            else:
                log.info("Announcer registered")
        else:
            log.info("Announcer already registered")

        # check if rewards can be distributed
        try:
            info = await rpc_client.upkeep_registry_reward(info=True)
        except httpx.ReadTimeout as err:
            log.error("Failed to get info on reward distribution due to ReadTimeout: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except ValueError as err:
            log.error("Failed to get info on reward distribution due to ValueError: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue
        except Exception as err:
            log.error("Failed to get info on reward distribution: %s", err)
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        if info["action_executable"] == True:
            log.info("Distributing rewards")
            try:
                response = await rpc_client.upkeep_registry_reward(target_puzzle_hash=TARGET_PUZZLE_HASH)
            except httpx.ReadTimeout as err:
                log.error("Failed to distribute rewards due to ReadTimeout: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except ValueError as err:
                log.error("Failed to distribute rewards due to ValueError: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            except Exception as err:
                log.error("Failed to distribute rewards: %s", err)
                await asyncio.sleep(CONTINUE_DELAY)
                continue
            else:
                log.info("Rewards distributed")
        else:
             log.info("Rewards cannot be distributed yet. Needs %s more Statutes price updates", info["statutes_price_updates_until_distributable"])

        if not success:
            await asyncio.sleep(CONTINUE_DELAY)
            continue

        # sleep until next run
        log.info("Sleeping for %s seconds", RUN_INTERVAL)
        await asyncio.sleep(RUN_INTERVAL)


def main():
    asyncio.run(run_announcer_rewards_bot())


if __name__ == "__main__":
    main()
