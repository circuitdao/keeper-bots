## Bot that bids in CircuitDAO recharge auctions
##
## A recharge auction recapitalises the protocol: it mints CRT and sells it for
## BYC (which tops up the treasury). Bidders compete by offering an ever higher
## CRT price - ie ever more BYC per CRT (equivalently, ever less CRT for the BYC
## they pay in) - and the highest-price bid wins the CRT.
##
## Note the direction is the mirror image of a surplus auction: there the bidder
## pays CRT for a fixed BYC lot and bids the price DOWN (more CRT per BYC); here
## the bidder pays BYC for CRT and bids the price UP (more BYC per CRT). A recharge
## bid therefore carries two amounts - the BYC paid in and the CRT requested - and
## the bidder wants a LOW price (little BYC per CRT), so the configured cap is a
## MAXIMUM price rather than a minimum.
##
## The bot:
## 1) monitors recharge auctions
## 2) for the first running, biddable auction, bids the minimum BYC amount and the
##    corresponding maximum CRT (ie the cheapest CRT price that still takes the
##    lead), provided that price does not exceed the configured maximum
## 3) does not outbid itself when it is already the leading bidder
##
## Bidding strategy:
## - By default the bot bids at the minimum price increment required to take the
##   lead (min BYC in, max CRT out).
## - Optionally, a maximum CRT/BYC price (ie BYC per CRT) can be set. The bot then
##   only bids while the price it would pay values each CRT at no more than that,
##   ie it never pays more than price BYC per CRT.
## - Optionally, a starting CRT/BYC price (ie BYC per CRT) can be set. Early in an
##   auction the bot then opens straight at that price (requesting fewer CRT for
##   the BYC it pays) to speed the auction along, instead of nudging the price up
##   by the minimum increment. The starting price must be <= the maximum CRT price
##   (checked at start-up).
## - Optionally, an absolute cap on the BYC bid amount (in mBYC) can be set.
##
## Prices may be fractional (eg 0.001).
##
## On start-up the bot checks that its polling intervals (RECHARGE_BID_RUN_INTERVAL
## and RECHARGE_BID_CONTINUE_DELAY) are short enough relative to the Recharge
## Auction bid TTL statute that it cannot sleep through an auction's bid window.

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

log = logging.getLogger("recharge_bid_bot")


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
    os.getenv("RECHARGE_BID_CONTINUE_DELAY")
)  # Wait (in seconds) before running again after a failed run
RUN_INTERVAL = int(
    os.getenv("RECHARGE_BID_RUN_INTERVAL")
)  # Wait (in seconds) between runs

# Maximum acceptable CRT/BYC price (ie BYC per CRT) we will pay for the CRT. Each
# CRT we receive costs us this much BYC at most, ie we will not bid at a price
# above this. May be a fractional value (eg 0.001). If blank/unset, we simply bid
# the minimum increment (subject to the absolute BYC cap below, if any).
_max_crt_price = _env("RECHARGE_BID_MAX_CRT_PRICE")
MAX_CRT_PRICE = float(_max_crt_price) if _max_crt_price else None

# Starting CRT/BYC price (ie BYC per CRT) at which to open our bidding. When set,
# early in an auction the bot opens straight at this price (requesting fewer CRT
# for the BYC it pays) rather than nudging up by the minimum increment, to speed
# the auction along. May be fractional. Must be <= RECHARGE_BID_MAX_CRT_PRICE
# (checked at start-up): a higher starting price means paying more BYC per CRT, so
# it must not breach the ceiling.
_starting_price = _env("RECHARGE_BID_STARTING_CRT_PRICE")
STARTING_PRICE = float(_starting_price) if _starting_price else None

# Absolute cap on the BYC amount (in mBYC) we are willing to pay in a bid. If
# blank/unset, there is no absolute cap on the bid amount.
_max_byc = _env("RECHARGE_BID_MAX_BYC_AMOUNT")
MAX_BYC_AMOUNT = int(_max_byc) if _max_byc else None

# Inner puzzle hash at which to receive the CRT if we win.
# If blank/unset, the keeper's own puzzle hash is used.
TARGET_PUZZLE_HASH = _env("RECHARGE_BID_TARGET_PUZZLE_HASH")


if not rpc_url:
    log.error("No URL found at which Circuit RPC server can be reached")
    raise ValueError("No URL found at which Circuit RPC server can be reached")
if not private_key:
    log.error("No master private key found")
    raise ValueError("No master private key found")
if (
    STARTING_PRICE is not None
    and MAX_CRT_PRICE is not None
    and STARTING_PRICE > MAX_CRT_PRICE
):
    log.error(
        "RECHARGE_BID_STARTING_CRT_PRICE (%s) must be <= RECHARGE_BID_MAX_CRT_PRICE (%s)",
        STARTING_PRICE,
        MAX_CRT_PRICE,
    )
    raise ValueError(
        "RECHARGE_BID_STARTING_CRT_PRICE must be <= RECHARGE_BID_MAX_CRT_PRICE"
    )

rpc_client = CircuitRPCClient(rpc_url, private_key, add_sig_data, fee_per_cost, key_count=int(os.getenv("KEY_COUNT", "500")))

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


async def run_recharge_bid_job() -> bool:
    """Run a single bid attempt. Returns True on error, False otherwise."""

    # resolve fee_per_cost (e.g. the "fast" preset) before submitting any
    # transaction; otherwise self.fee_per_cost stays None and the API rejects
    # the bid with a fee_per_cost float_type error
    try:
        await rpc_client.set_fee_per_cost()
    except Exception as err:
        log.error("Failed to set fee_per_cost: %s", str(err))
        return True

    # get recharge auction coins
    try:
        recharge_coins = await rpc_client.upkeep_recharge_list()
    except httpx.ReadTimeout as err:
        log.error("Failed to list Recharge Auction coins due to ReadTimeout: %s", err)
        return True
    except Exception as err:
        log.error("Failed to list Recharge Auction coins: %s", err)
        return True

    # find a running auction that can still be bid on (STANDBY auctions have not
    # started, and an expired running auction can no longer be bid on)
    biddable = [
        c for c in recharge_coins if c["status"] == "RUNNING" and not c["expired"]
    ]
    if not biddable:
        log.info("No biddable Recharge Auction")
        return False

    # bid on the first biddable auction we are not already leading; leave the rest
    # for subsequent runs (avoids reusing the same BYC coins across unconfirmed bids)
    auction = None
    for c in biddable:
        last_bid = c["last_bid"]
        if last_bid is not None and last_bid["target_puzzle_hash"] in my_puzzle_hashes:
            log.info(
                "Already the leading bidder on Recharge Auction %s. Skipping", c["name"]
            )
            continue
        auction = c
        break
    if auction is None:
        log.info("Already the leading bidder on all biddable Recharge Auctions. Nothing to do")
        return False
    auction_name = auction["name"]

    # find out what the minimum bid is (min BYC in, max CRT out = cheapest price
    # that still takes the lead)
    try:
        bid_info = await rpc_client.upkeep_recharge_bid(auction_name, info=True)
    except httpx.ReadTimeout as err:
        log.error(
            "Failed to get bid info for Recharge Auction %s due to ReadTimeout: %s",
            auction_name,
            err,
        )
        return True
    except Exception as err:
        log.error(
            "Failed to get bid info for Recharge Auction %s: %s", auction_name, err
        )
        return True

    if bid_info["auction_expired"] or not bid_info["action_executable"]:
        log.info("Recharge Auction %s cannot be bid on right now", auction_name)
        return False

    byc_amount = bid_info["byc_amount_to_bid"]  # minimum BYC we must pay in
    crt_max = bid_info["crt_amount_to_request"]  # max CRT for that BYC = cheapest price
    if not byc_amount or not crt_max:
        log.info("Recharge Auction %s has no valid minimum bid. Skipping", auction_name)
        return False

    # respect the configured absolute cap on how much BYC we are prepared to pay
    if MAX_BYC_AMOUNT is not None and byc_amount > MAX_BYC_AMOUNT:
        log.info(
            "Minimum bid of %s mBYC on Recharge Auction %s exceeds cap of %s mBYC. Skipping",
            byc_amount,
            auction_name,
            MAX_BYC_AMOUNT,
        )
        return False

    # cheapest CRT/BYC price we could bid at (mBYC / mCRT = BYC per CRT). Bidding
    # the max CRT for the min BYC yields the lowest, ie most favourable, price.
    min_price = byc_amount / crt_max

    # respect the configured maximum CRT/BYC price: don't bid at a price that values
    # each CRT above the ceiling. If even the cheapest valid bid is too expensive,
    # there is nothing we can do this round.
    if MAX_CRT_PRICE is not None and min_price > MAX_CRT_PRICE:
        log.info(
            "Cheapest bid on Recharge Auction %s is %s CRT/BYC, above cap of %s CRT/BYC. Skipping",
            auction_name,
            format_sig(min_price),
            MAX_CRT_PRICE,
        )
        return False

    # decide the price to bid at. By default we bid at the minimum price (max CRT).
    # If a starting price is configured and the auction has not yet escalated past
    # it, we open at the starting price (requesting fewer CRT) to speed the auction
    # along; once bidding has pushed the minimum price above it, we fall back to the
    # minimum. STARTING_PRICE <= MAX_CRT_PRICE (enforced at start-up), so opening
    # here respects the ceiling.
    crt_amount = crt_max
    if STARTING_PRICE is not None and STARTING_PRICE > min_price:
        crt_starting = int(byc_amount / STARTING_PRICE)  # fewer CRT => higher price
        # never request fewer CRT than the cheapest valid bid allows more of, and
        # never fewer than 1
        crt_amount = max(min(crt_amount, crt_starting), 1)

    bid_price = byc_amount / crt_amount

    # rounding safety: never let the actual price exceed the ceiling. If flooring
    # nudged it over, fall back to the cheapest valid bid (which is <= the ceiling).
    if MAX_CRT_PRICE is not None and bid_price > MAX_CRT_PRICE:
        crt_amount = crt_max
        bid_price = byc_amount / crt_amount

    log.info(
        "Bidding %s mBYC for %s mCRT (%s CRT/BYC) on Recharge Auction %s",
        byc_amount,
        crt_amount,
        format_sig(bid_price),
        auction_name,
    )

    # place the bid. byc_amount and crt_amount are ints in mBYC/mCRT and pass
    # through the client unit conversion unchanged, ie they are bid as-is.
    try:
        await rpc_client.upkeep_recharge_bid(
            auction_name,
            amount=byc_amount,
            crt=crt_amount,
            target_puzzle_hash=target_puzzle_hash,
        )
    except httpx.ReadTimeout as err:
        log.error(
            "Failed to bid on Recharge Auction %s due to ReadTimeout: %s",
            auction_name,
            err,
        )
        return True
    except (APIError, ValueError) as err:
        log.error("Failed to bid on Recharge Auction %s: %s", auction_name, err)
        return True
    except Exception as err:
        log.error("Failed to bid on Recharge Auction %s: %s", auction_name, err)
        return True

    log.info(
        "Bid %s mBYC for %s mCRT (%s CRT/BYC) on Recharge Auction %s",
        byc_amount,
        crt_amount,
        format_sig(bid_price),
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

    A recharge auction expires bid_ttl seconds after the last bid. If the bot's
    sleep interval is too long relative to bid_ttl, a competing bid could time
    the auction out before the bot next polls and gets a chance to bid.
    """
    try:
        statutes = await rpc_client.statutes_list()
        bid_ttl = int(statutes["implemented_statutes"]["RECHARGE_AUCTION_BID_TTL"])
    except Exception as err:
        # Can't confirm the intervals are safe, so refuse to run rather than risk
        # sleeping through an auction's bid window.
        log.error(
            "Could not verify polling intervals against Recharge Auction bid TTL: %s",
            err,
        )
        raise ValueError(
            "Could not verify polling intervals against Recharge Auction bid TTL"
        ) from err

    max_interval = max(RUN_INTERVAL, CONTINUE_DELAY)
    allowed = TTL_INTERVAL_FRACTION * bid_ttl
    if max_interval > allowed:
        log.error(
            "RECHARGE_BID_RUN_INTERVAL (%s) and RECHARGE_BID_CONTINUE_DELAY (%s) must each be <= %s"
            " seconds (%s of the Recharge Auction bid TTL of %s s), otherwise the bot may miss an"
            " auction's bid window",
            RUN_INTERVAL,
            CONTINUE_DELAY,
            allowed,
            TTL_INTERVAL_FRACTION,
            bid_ttl,
        )
        raise ValueError(
            f"Polling intervals too long for Recharge Auction bid TTL of {bid_ttl} s"
            f" (max allowed {allowed} s)"
        )
    log.info(
        "Polling intervals OK: max(%s, %s) s <= %s s (bid TTL %s s)",
        RUN_INTERVAL,
        CONTINUE_DELAY,
        allowed,
        bid_ttl,
    )


async def run_recharge_bid_bot():
    await check_intervals_against_bid_ttl()
    while True:
        errored = await run_recharge_bid_job()
        delay = CONTINUE_DELAY if errored else RUN_INTERVAL
        log.info("Sleeping for %s seconds", delay)
        await asyncio.sleep(delay)


def main():
    try:
        asyncio.run(run_recharge_bid_bot())
    except KeyboardInterrupt:
        log.info("Received KeyboardInterrupt. Shutting down recharge bid bot")
    except ValueError as err:
        # Startup validation failed (eg polling intervals too long for the bid
        # TTL). Terminate with a non-zero exit code instead of running.
        log.error("Recharge bid bot terminating due to configuration error: %s", err)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
