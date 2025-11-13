"""
OKX exchange price feed implementation.
"""
import asyncio
import json
import random
import time
import statistics
import aiohttp
import math
import logging

from keeper_bots.price_feeds.base_oracle import BaseOracleFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"


async def okx_ws(oracle, feed_instance=None):
    """
    OKX WebSocket handler for trade and orderbook data.

    Args:
        oracle: Oracle instance to update with trades
        feed_instance: OracleFeed instance to update with book data (optional)
    """
    # Create subscription channels based on provided trading pairs
    trade_ch = {"op": "subscribe", "args": [{"channel": "trades", "instId": p} for p in oracle.trading_pairs]}
    book_ch = {"op": "subscribe", "args": [{"channel": "books5", "instId": p} for p in oracle.trading_pairs]}

    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(OKX_WS, heartbeat=15) as ws:
                    await ws.send_json(trade_ch)
                    await ws.send_json(book_ch)
                    book_mids = {}
                    logging.info("Connected to OKX WebSocket and subscribed to channels.")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)
                        if "event" in data:
                            if data["event"] == "subscribe":
                                logging.info("Successfully subscribed to %s", data["arg"])
                                # Set start time when first subscription is successful
                                if oracle.start_time is None:
                                    oracle.start_time = time.time()
                                    logging.info("Oracle startup window: %d seconds", oracle.startup_window)
                            elif data["event"] == "error":
                                logging.error("Subscription error: %s", data["msg"])
                            continue
                        if "arg" not in data:
                            continue

                        ch = data["arg"]["channel"]
                        instId = data["arg"]["instId"]

                        if ch == "trades" and "data" in data:
                            for t in data["data"]:
                                px = float(t["px"])
                                sz = float(t["sz"])
                                ts = int(t["ts"])
                                oracle.add_trade(instId, ts, px, sz)
                        elif ch == "books5":
                            if data.get("data"):
                                d = data["data"][0]
                                bids = d.get("bids")
                                asks = d.get("asks")
                                if bids and asks and len(bids) > 0 and len(asks) > 0:
                                    best_bid = float(bids[0][0])
                                    best_ask = float(asks[0][0])
                                    mid = (best_bid + best_ask) / 2.0

                                    # Use volume at the top of the book for weighting
                                    top_bid_vol = float(bids[0][1])
                                    top_ask_vol = float(asks[0][1])
                                    book_top_vol = top_bid_vol + top_ask_vol

                                    if instId.endswith("-USDT"):
                                        if not math.isnan(oracle.usdt_usd_price):
                                            mid_usd = mid * oracle.usdt_usd_price
                                            book_mids[instId] = (mid_usd, book_top_vol)
                                            # Also update feed_instance book_mids if available
                                            if feed_instance is not None:
                                                feed_instance.book_mids[instId] = (mid_usd, book_top_vol)
                                        elif instId in book_mids:
                                            # remove stale price if conversion not possible
                                            del book_mids[instId]
                                            if feed_instance is not None and instId in feed_instance.book_mids:
                                                del feed_instance.book_mids[instId]
                                    else:  # -USD
                                        book_mids[instId] = (mid, book_top_vol)
                                        # Also update feed_instance book_mids if available
                                        if feed_instance is not None:
                                            feed_instance.book_mids[instId] = (mid, book_top_vol)
                        # publish every 10s
                        now = time.time()
                        if now - oracle.last_pub >= 10:
                            fallback_mid = None
                            if book_mids:
                                # Calculate a volume-weighted mid-price for fallback
                                total_book_volume = sum(vol for _, vol in book_mids.values())
                                if total_book_volume > 0:
                                    weighted_sum = sum(px * vol for px, vol in book_mids.values())
                                    fallback_mid = weighted_sum / total_book_volume
                                else:
                                    # Fallback to simple average if no volume
                                    prices = [px for px, _ in book_mids.values()]
                                    if prices:
                                        fallback_mid = statistics.mean(prices)
                            price, meta = oracle.compute(fallback_mid=fallback_mid)

                            price_str = (
                                f"{price:.2f}" if isinstance(price, (int, float)) and not math.isnan(price) else "None"
                            )
                            usdt_price_str = (
                                f"{oracle.usdt_usd_price:.4f}" if not math.isnan(oracle.usdt_usd_price) else "None"
                            )
                            logging.info(
                                "%s-USD oracle=%s trades=%d meta=%s | USDT-USD=%s",
                                oracle.base_currency,
                                price_str,
                                meta["trades"],
                                meta,
                                usdt_price_str,
                            )
                            oracle.last_pub = now
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.error("OKX WebSocket connection error: %s. Reconnecting...", e)
            await asyncio.sleep(2 + 3 * random.random())


class OkxOracleFeed(BaseOracleFeed):
    """
    OKX exchange price feed.
    """
    def __init__(self, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize the OKX Oracle Feed.

        Args:
            trading_pairs: List of trading pairs, e.g., ["XCH-USDT", "XCH-USD"]
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds (time before prices are considered valid)
            min_notional: Minimum notional value for trades
        """
        super().__init__("OKX", trading_pairs, window_sec, startup_window_sec, min_notional)

    async def _create_websocket_task(self):
        """Create the OKX WebSocket task."""
        await okx_ws(self.oracle, self)


async def main():
    # Example usage with XCH pairs
    trading_pairs = ["XCH-USDT", "XCH-USD"]

    async with OkxOracleFeed(trading_pairs) as feed:
        while True:
            price, meta = await feed.get_price()
            price_str = f"{price:.2f}" if not math.isnan(price) else "None"
            logging.info("Current price: %s, meta: %s", price_str, meta)
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nExiting.")
