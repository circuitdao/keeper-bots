## Bot that bids in CircuitDAO surplus auctions
##
## A surplus auction sells a fixed lot of BYC (protocol surplus) in exchange
## for CRT, which is subsequently burned. Bidders compete by offering ever
## larger amounts of CRT for the fixed BYC lot; the highest bidder wins the lot.
##
## The bot:
## 1) monitors surplus auctions
## 2) for the (single) active auction, bids the minimum CRT amount required to
##    take the lead, provided that amount does not exceed the configured caps
## 3) does not outbid itself when it is already the leading bidder
##
## Bidding strategy:
## - By default the bot bids the minimum CRT increment required to take the lead.
## - Optionally, a minimum CRT/BYC price (ie BYC per CRT) can be set. The bot
##   then only bids while the BYC lot values each CRT it pays at no less than that
##   price, ie it never bids more than lot_amount / price CRT for the lot.
## - Optionally, a starting CRT/BYC price (ie BYC per CRT) can be set. Early in an
##   auction the bot then bids straight to lot_amount / starting_price CRT to speed
##   the auction along, instead of nudging up by the minimum increment. The starting
##   price must be >= the minimum CRT price (checked at start-up).
## - Optionally, an absolute cap on the CRT bid amount (in mCRT) can be set.
##
## Prices may be fractional (eg 0.001).
##
## On start-up the bot checks that its polling intervals (SURPLUS_BID_RUN_INTERVAL
## and SURPLUS_BID_CONTINUE_DELAY) are short enough relative to the Surplus Auction
## bid TTL statute that it cannot sleep through an auction's bid window.
##
## NOTE: This bot assumes at most one active surplus auction at a time.

import os
import math
import asyncio
import httpx
import yaml
import logging.config
from dotenv import load_dotenv

from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    puzzle_hash_for_synthetic_public_key,
)

from circuit_cli.client import APIError, CircuitRPCClient


if os.path.exists("log_conf.yaml"):
    with open("log_conf.yaml", "r") as f:
        config = yaml.safe_load(f)
        logging.config.dictConfig(config)

log = logging.getLogger("surplus_bid_bot")


# .env lives at the keeper-bots repo root (one level above this package dir)
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)


def _env(name: str) -> str | None:
    """Read an optional env var, tolerating an inline '# ...' comment.

    python-dotenv leaves the comment as the value for a blank key written as
    ``KEY= # comment``, so strip any '#' comment and surrounding whitespace and
    treat an empty result as unset. Safe here because these values are numbers
    or hex, never containing a literal '#'.
    """
    val = os.getenv(name)
    if val is None:
        return None
    return val.split("#", 1)[0].strip() or None


rpc_url = str(os.getenv("RPC_URL"))  # Base URL for Circuit RPC API server
private_key = str(
    os.getenv("PRIVATE_KEY")
)  # Private master key that controls the keeper
add_sig_data = str(
    os.getenv("ADD_SIG_DATA")
)  # Additional signature data (depends on network)
fee_per_cost = os.getenv("FEE_PER_COST")  # Fee per cost for transactions

CONTINUE_DELAY = int(
    os.getenv("SURPLUS_BID_CONTINUE_DELAY")
)  # Wait (in seconds) before running again after a failed run
RUN_INTERVAL = int(
    os.getenv("SURPLUS_BID_RUN_INTERVAL")
)  # Wait (in seconds) between runs

# Minimum acceptable CRT/BYC price (ie BYC per CRT) for the CRT we bid. The BYC
# lot we win must value each CRT we pay at no less than this, ie we will not bid
# more than lot_amount / price CRT. May be a fractional value (eg 0.001). If
# blank/unset, we simply bid the minimum increment (subject to the absolute
# amount cap below, if any).
_min_crt_price = _env("SURPLUS_BID_MIN_CRT_PRICE")
MIN_CRT_PRICE = float(_min_crt_price) if _min_crt_price else None

# Starting CRT/BYC price (ie BYC per CRT) at which to open our bidding. When set,
# early in an auction the bot bids straight to lot_amount / starting_price CRT
# (rather than the minimum increment) to speed the auction along. May be
# fractional. Must be >= SURPLUS_BID_MIN_CRT_PRICE (checked at start-up): a lower
# starting price implies a larger opening bid, so it must not breach the floor.
_starting_price = _env("SURPLUS_BID_STARTING_CRT_PRICE")
STARTING_PRICE = float(_starting_price) if _starting_price else None

# Absolute cap on the CRT amount (in mCRT) we are willing to bid for the BYC lot.
# If blank/unset, there is no absolute cap on the bid amount.
_max_crt = _env("SURPLUS_BID_MAX_CRT_AMOUNT")
MAX_CRT_AMOUNT = int(_max_crt) if _max_crt else None

# Inner puzzle hash at which to receive the BYC lot if we win.
# If blank/unset, the keeper's own puzzle hash is used.
TARGET_PUZZLE_HASH = _env("SURPLUS_BID_TARGET_PUZZLE_HASH")


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")
if (
    STARTING_PRICE is not None
    and MIN_CRT_PRICE is not None
    and STARTING_PRICE < MIN_CRT_PRICE
):
    log.error(
        "SURPLUS_BID_STARTING_CRT_PRICE (%s) must be >= SURPLUS_BID_MIN_CRT_PRICE (%s)",
        STARTING_PRICE,
        MIN_CRT_PRICE,
    )
    raise ValueError(
        "SURPLUS_BID_STARTING_CRT_PRICE must be >= SURPLUS_BID_MIN_CRT_PRICE"
    )

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost)

# Puzzle hashes controlled by us, used to detect when we are already the leading bidder.
my_puzzle_hashes = {
    puzzle_hash_for_synthetic_public_key(pk).hex()
    for pk in rpc_client.synthetic_public_keys
}
# Default target: keeper's own puzzle hash (the first synthetic key).
default_target_puzzle_hash = puzzle_hash_for_synthetic_public_key(
    rpc_client.synthetic_public_keys[0]
).hex()
target_puzzle_hash = TARGET_PUZZLE_HASH or default_target_puzzle_hash
# Make sure we recognise our own bids even when proceeds are directed elsewhere.
my_puzzle_hashes.add(target_puzzle_hash)


def format_sig(value: float, sig: int = 3) -> str:
    """Format a float to `sig` significant figures, without scientific notation."""
    if value == 0:
        return "0"
    decimals = max(sig - 1 - math.floor(math.log10(abs(value))), 0)
    return f"{value:.{decimals}f}"


async def run_surplus_bid_job() -> bool:
    """Run a single bid attempt. Returns True on error, False otherwise."""

    # get surplus auction coins
    try:
        surplus_coins = await rpc_client.upkeep_surplus_list()
    except httpx.ReadTimeout as err:
        log.error("Failed to list Surplus Auction coins due to ReadTimeout: %s", err)
        return True
    except Exception as err:
        log.error("Failed to list Surplus Auction coins: %s", err)
        return True

    # find the (single) active auction, ie one that has not yet expired
    active_auctions = [c for c in surplus_coins if not c["expired"]]
    if not active_auctions:
        log.info("No active Surplus Auction to bid on")
        return False
    if len(active_auctions) > 1:
        log.warning(
            "Found %s active Surplus Auctions, expected at most one. Bidding on the first",
            len(active_auctions),
        )
    auction = active_auctions[0]
    auction_name = auction["name"]

    # do not outbid ourselves if we are already the leading bidder
    last_bid = auction["last_bid"]
    if last_bid is not None and last_bid["target_puzzle_hash"] in my_puzzle_hashes:
        log.info(
            "Already the leading bidder on Surplus Auction %s. Nothing to do",
            auction_name,
        )
        return False

    # find out what the minimum bid is
    try:
        bid_info = await rpc_client.upkeep_surplus_bid(auction_name, info=True)
    except httpx.ReadTimeout as err:
        log.error(
            "Failed to get bid info for Surplus Auction %s due to ReadTimeout: %s",
            auction_name,
            err,
        )
        return True
    except Exception as err:
        log.error(
            "Failed to get bid info for Surplus Auction %s: %s", auction_name, err
        )
        return True

    if bid_info["auction_expired"] or not bid_info["action_executable"]:
        log.info("Surplus Auction %s cannot be bid on right now", auction_name)
        return False

    min_crt_amount = bid_info["min_crt_amount_to_bid"]
    lot_amount = auction["byc_lot_amount"]

    # respect the configured minimum CRT/BYC price: don't bid so much CRT that the
    # BYC lot values each CRT below the floor. lot_amount is in mBYC and the price
    # is in BYC per CRT, so the cap is lot_amount / price mCRT. We test it as
    # (min_crt_amount * price > lot_amount) rather than dividing by the price, so
    # that small fractional prices (eg 0.001) don't introduce rounding errors.
    if MIN_CRT_PRICE is not None and min_crt_amount * MIN_CRT_PRICE > lot_amount:
        log.info(
            "Minimum bid of %s mCRT on Surplus Auction %s would value CRT below %s CRT/BYC. Skipping",
            min_crt_amount,
            auction_name,
            MIN_CRT_PRICE,
        )
        return False

    # respect the configured absolute cap on how much CRT we are prepared to pay
    if MAX_CRT_AMOUNT is not None and min_crt_amount > MAX_CRT_AMOUNT:
        log.info(
            "Minimum bid of %s mCRT on Surplus Auction %s exceeds cap of %s mCRT. Skipping",
            min_crt_amount,
            auction_name,
            MAX_CRT_AMOUNT,
        )
        return False

    # decide how much to bid. By default we bid the minimum increment. If a
    # starting price is configured, we bid straight to that level early in the
    # auction to speed it up; once bidding has escalated past it, max() falls
    # back to the minimum increment so we never overbid late. STARTING_PRICE >=
    # MIN_CRT_PRICE (enforced at start-up), so the starting bid respects the floor.
    bid_amount = min_crt_amount
    if STARTING_PRICE is not None:
        starting_crt_amount = int(lot_amount / STARTING_PRICE)
        bid_amount = max(bid_amount, starting_crt_amount)
        if MAX_CRT_AMOUNT is not None:
            bid_amount = min(bid_amount, MAX_CRT_AMOUNT)

    # equivalent CRT/BYC price of this bid (mBYC / mCRT = BYC per CRT)
    bid_crt_price = lot_amount / bid_amount

    log.info(
        "Bidding %s mCRT (%s CRT/BYC) for %s mBYC lot on Surplus Auction %s",
        bid_amount,
        format_sig(bid_crt_price),
        lot_amount,
        auction_name,
    )

    # place the bid. bid_amount is an int in mCRT and passes through the client
    # unit conversion unchanged, ie it is bid as-is.
    try:
        await rpc_client.upkeep_surplus_bid(
            auction_name, amount=bid_amount, target_puzzle_hash=target_puzzle_hash
        )
    except httpx.ReadTimeout as err:
        log.error(
            "Failed to bid on Surplus Auction %s due to ReadTimeout: %s",
            auction_name,
            err,
        )
        return True
    except (APIError, ValueError) as err:
        log.error("Failed to bid on Surplus Auction %s: %s", auction_name, err)
        return True
    except Exception as err:
        log.error("Failed to bid on Surplus Auction %s: %s", auction_name, err)
        return True

    log.info(
        "Bid %s mCRT (%s CRT/BYC) on Surplus Auction %s",
        bid_amount,
        format_sig(bid_crt_price),
        auction_name,
    )
    return False


# Fraction of the bid TTL that a single sleep interval is allowed to consume.
# The remainder is headroom for the bot to build, submit and confirm a bid once
# it spots a competing bid. Both RUN_INTERVAL and CONTINUE_DELAY must fit within
# TTL_INTERVAL_FRACTION * bid_ttl, otherwise the bot could sleep through an
# auction's bid window and miss its chance to bid.
TTL_INTERVAL_FRACTION = 0.5


async def check_intervals_against_bid_ttl():
    """Ensure the polling intervals are short enough not to miss an auction.

    A surplus auction expires bid_ttl seconds after the last bid. If the bot's
    sleep interval is too long relative to bid_ttl, a competing bid could time
    the auction out before the bot next polls and gets a chance to bid.
    """
    try:
        statutes = await rpc_client.statutes_list()
        bid_ttl = int(statutes["implemented_statutes"]["SURPLUS_AUCTION_BID_TTL"])
    except Exception as err:
        # Can't confirm the intervals are safe, so refuse to run rather than risk
        # sleeping through an auction's bid window.
        log.error(
            "Could not verify polling intervals against Surplus Auction bid TTL: %s",
            err,
        )
        raise ValueError(
            "Could not verify polling intervals against Surplus Auction bid TTL"
        ) from err

    max_interval = max(RUN_INTERVAL, CONTINUE_DELAY)
    allowed = TTL_INTERVAL_FRACTION * bid_ttl
    if max_interval > allowed:
        log.error(
            "SURPLUS_BID_RUN_INTERVAL (%s) and SURPLUS_BID_CONTINUE_DELAY (%s) must each be <= %s"
            " seconds (%s of the Surplus Auction bid TTL of %s s), otherwise the bot may miss an"
            " auction's bid window",
            RUN_INTERVAL,
            CONTINUE_DELAY,
            allowed,
            TTL_INTERVAL_FRACTION,
            bid_ttl,
        )
        raise ValueError(
            f"Polling intervals too long for Surplus Auction bid TTL of {bid_ttl} s"
            f" (max allowed {allowed} s)"
        )
    log.info(
        "Polling intervals OK: max(%s, %s) s <= %s s (bid TTL %s s)",
        RUN_INTERVAL,
        CONTINUE_DELAY,
        allowed,
        bid_ttl,
    )


async def run_surplus_bid_bot():
    await check_intervals_against_bid_ttl()
    while True:
        errored = await run_surplus_bid_job()
        delay = CONTINUE_DELAY if errored else RUN_INTERVAL
        log.info("Sleeping for %s seconds", delay)
        await asyncio.sleep(delay)


def main():
    try:
        asyncio.run(run_surplus_bid_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down surplus bid bot")
    except ValueError as err:
        # Startup validation failed (eg polling intervals too long for the bid
        # TTL). Terminate with a non-zero exit code instead of running.
        log.error("Surplus bid bot terminating due to configuration error: %s", err)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
