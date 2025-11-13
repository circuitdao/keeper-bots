"""
Gate.io exchange price feed implementation.
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

GATE_WS = "wss://api.gateio.ws/ws/v4/"


async def gate_ws(oracle, feed_instance=None):
    """
    Gate.io WebSocket handler for trade and orderbook data.

    Args:
        oracle: Oracle instance to update with trades
        feed_instance: OracleFeed instance to update with book data (optional)
    """
    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(GATE_WS, heartbeat=15) as ws:
                    # Subscribe to trades and book ticker
                    for pair in oracle.trading_pairs:
                        await ws.send_json({
                            "time": int(time.time()),
                            "channel": "spot.trades",
                            "event": "subscribe",
                            "payload": [pair]
                        })
                        await ws.send_json({
                            "time": int(time.time()),
                            "channel": "spot.book_ticker",
                            "event": "subscribe",
                            "payload": [pair]
                        })

                    book_mids = {}
                    logging.info("Connected to Gate.io WebSocket and subscribed to channels.")

                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)

                        # Handle subscription confirmation
                        if data.get("event") == "subscribe":
                            if data.get("result", {}).get("status") == "success":
                                logging.info("Successfully subscribed to Gate.io %s for %s",
                                           data["channel"], data.get("result", {}).get("payload", []))
                                # Set start time when first subscription is successful
                                if oracle.start_time is None:
                                    oracle.start_time = time.time()
                                    logging.info("Oracle startup window: %d seconds", oracle.startup_window)

                        # Handle trades
                        if data.get("channel") == "spot.trades" and data.get("event") == "update":
                            result = data.get("result")
                            if result:
                                px = float(result["price"])
                                sz = float(result["amount"])
                                ts = int(result["create_time"]) * 1000  # Convert to milliseconds
                                currency_pair = result["currency_pair"]
                                oracle.add_trade(currency_pair, ts, px, sz)

                        # Handle book ticker
                        elif data.get("channel") == "spot.book_ticker" and data.get("event") == "update":
                            result = data.get("result")
                            if result:
                                best_bid = float(result["b"])
                                best_ask = float(result["a"])
                                bid_vol = float(result["B"])
                                ask_vol = float(result["A"])
                                mid = (best_bid + best_ask) / 2.0
                                book_top_vol = bid_vol + ask_vol
                                currency_pair = result["s"]

                                if currency_pair.endswith("_USDT"):
                                    if not math.isnan(oracle.usdt_usd_price):
                                        mid_usd = mid * oracle.usdt_usd_price
                                        book_mids[currency_pair] = (mid_usd, book_top_vol)
                                        if feed_instance is not None:
                                            feed_instance.book_mids[currency_pair] = (mid_usd, book_top_vol)
                                    elif currency_pair in book_mids:
                                        del book_mids[currency_pair]
                                        if feed_instance is not None and currency_pair in feed_instance.book_mids:
                                            del feed_instance.book_mids[currency_pair]
                                else:  # _USD
                                    book_mids[currency_pair] = (mid, book_top_vol)
                                    if feed_instance is not None:
                                        feed_instance.book_mids[currency_pair] = (mid, book_top_vol)

                        # publish every 10s
                        now = time.time()
                        if now - oracle.last_pub >= 10:
                            fallback_mid = None
                            if book_mids:
                                total_book_volume = sum(vol for _, vol in book_mids.values())
                                if total_book_volume > 0:
                                    weighted_sum = sum(px * vol for px, vol in book_mids.values())
                                    fallback_mid = weighted_sum / total_book_volume
                                else:
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
                                "Gate.io %s-USD oracle=%s trades=%d meta=%s | USDT-USD=%s",
                                oracle.base_currency,
                                price_str,
                                meta["trades"],
                                meta,
                                usdt_price_str,
                            )
                            oracle.last_pub = now
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.error("Gate.io WebSocket connection error: %s. Reconnecting...", e)
            await asyncio.sleep(2 + 3 * random.random())


class GateOracleFeed(BaseOracleFeed):
    """
    Gate.io exchange price feed.
    """
    def __init__(self, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize the Gate.io Oracle Feed.

        Args:
            trading_pairs: List of trading pairs, e.g., ["XCH_USDT"] (note: Gate.io uses underscore)
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds (time before prices are considered valid)
            min_notional: Minimum notional value for trades
        """
        super().__init__("Gate.io", trading_pairs, window_sec, startup_window_sec, min_notional)

    async def _create_websocket_task(self):
        """Create the Gate.io WebSocket task."""
        await gate_ws(self.oracle, self)


async def main():
    # Example usage with XCH pairs
    trading_pairs = ["XCH_USDT"]

    async with GateOracleFeed(trading_pairs) as feed:
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
