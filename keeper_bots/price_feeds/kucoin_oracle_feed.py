"""
KuCoin exchange price feed implementation.
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

KUCOIN_REST_API = "https://api.kucoin.com"
KUCOIN_TOKEN_ENDPOINT = "/api/v1/bullet-public"


async def get_kucoin_token():
    """Get KuCoin WebSocket token and server info."""
    async with aiohttp.ClientSession() as session:
        url = f"{KUCOIN_REST_API}{KUCOIN_TOKEN_ENDPOINT}"
        async with session.post(url) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("code") == "200000":
                    token = data["data"]["token"]
                    servers = data["data"]["instanceServers"]
                    if servers:
                        server = servers[0]
                        ws_endpoint = server["endpoint"]
                        ping_interval = server.get("pingInterval", 18000)
                        ping_timeout = server.get("pingTimeout", 10000)
                        return ws_endpoint, token, ping_interval, ping_timeout
            raise Exception(f"Failed to get KuCoin token: {response.status}")


async def kucoin_ws(oracle, feed_instance=None):
    """
    KuCoin WebSocket handler for trade and orderbook data.

    Args:
        oracle: Oracle instance to update with trades
        feed_instance: OracleFeed instance to update with book data (optional)
    """
    while True:
        try:
            # Get WebSocket token and server info
            ws_endpoint, token, ping_interval, ping_timeout = await get_kucoin_token()
            ws_url = f"{ws_endpoint}?token={token}"

            logging.info("Connecting to KuCoin WebSocket: %s", ws_endpoint)

            async with aiohttp.ClientSession() as sess:
                # KuCoin uses its own ping/pong mechanism
                async with sess.ws_connect(ws_url, heartbeat=None) as ws:
                    # Subscribe to match execution (trades) and ticker for each pair
                    connect_id = str(int(time.time() * 1000))
                    for pair in oracle.trading_pairs:
                        await ws.send_json({
                            "id": connect_id,
                            "type": "subscribe",
                            "topic": f"/market/match:{pair}",
                            "privateChannel": False,
                            "response": True
                        })
                        await ws.send_json({
                            "id": connect_id,
                            "type": "subscribe",
                            "topic": f"/market/ticker:{pair}",
                            "privateChannel": False,
                            "response": True
                        })

                    book_mids = {}
                    last_ping = time.time()
                    logging.info("Connected to KuCoin WebSocket and subscribed to channels.")

                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue

                        data = json.loads(msg.data)
                        msg_type = data.get("type")

                        # Handle welcome message
                        if msg_type == "welcome":
                            logging.info("Received KuCoin welcome message")
                            if oracle.start_time is None:
                                oracle.start_time = time.time()
                                logging.info("Oracle startup window: %d seconds", oracle.startup_window)

                        # Handle subscription acknowledgment
                        elif msg_type == "ack":
                            logging.info("KuCoin subscription acknowledged")

                        # Handle pong response
                        elif msg_type == "pong":
                            pass

                        # Handle trade matches
                        elif msg_type == "message":
                            topic = data.get("topic", "")

                            if "/market/match:" in topic:
                                trade_data = data.get("data", {})
                                if trade_data:
                                    px = float(trade_data["price"])
                                    sz = float(trade_data["size"])
                                    # KuCoin timestamp is in nanoseconds
                                    ts_ns = int(trade_data["time"])
                                    ts_ms = ts_ns // 1000000
                                    symbol = trade_data["symbol"]
                                    oracle.add_trade(symbol, ts_ms, px, sz)

                            # Handle ticker updates (best bid/ask)
                            elif "/market/ticker:" in topic:
                                ticker_data = data.get("data", {})
                                if ticker_data:
                                    best_bid = ticker_data.get("bestBid")
                                    best_ask = ticker_data.get("bestAsk")
                                    best_bid_size = ticker_data.get("bestBidSize")
                                    best_ask_size = ticker_data.get("bestAskSize")

                                    if best_bid and best_ask:
                                        best_bid = float(best_bid)
                                        best_ask = float(best_ask)
                                        bid_vol = float(best_bid_size) if best_bid_size else 0
                                        ask_vol = float(best_ask_size) if best_ask_size else 0
                                        mid = (best_bid + best_ask) / 2.0
                                        book_top_vol = bid_vol + ask_vol
                                        symbol = topic.split(":")[-1]

                                        if symbol.endswith("-USDT"):
                                            if not math.isnan(oracle.usdt_usd_price):
                                                mid_usd = mid * oracle.usdt_usd_price
                                                book_mids[symbol] = (mid_usd, book_top_vol)
                                                if feed_instance is not None:
                                                    feed_instance.book_mids[symbol] = (mid_usd, book_top_vol)
                                            elif symbol in book_mids:
                                                del book_mids[symbol]
                                                if feed_instance is not None and symbol in feed_instance.book_mids:
                                                    del feed_instance.book_mids[symbol]
                                        else:  # -USD
                                            book_mids[symbol] = (mid, book_top_vol)
                                            if feed_instance is not None:
                                                feed_instance.book_mids[symbol] = (mid, book_top_vol)

                        # Send ping to keep connection alive
                        now = time.time()
                        if now - last_ping >= ping_interval / 2000:
                            await ws.send_json({
                                "id": str(int(now * 1000)),
                                "type": "ping"
                            })
                            last_ping = now

                        # Publish price every 10s
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
                                "KuCoin %s-USD oracle=%s trades=%d meta=%s | USDT-USD=%s",
                                oracle.base_currency,
                                price_str,
                                meta["trades"],
                                meta,
                                usdt_price_str,
                            )
                            oracle.last_pub = now

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.error("KuCoin WebSocket connection error: %s. Reconnecting...", e)
            await asyncio.sleep(2 + 3 * random.random())
        except Exception as e:
            logging.error("KuCoin WebSocket unexpected error: %s. Reconnecting...", e)
            await asyncio.sleep(2 + 3 * random.random())


class KucoinOracleFeed(BaseOracleFeed):
    """
    KuCoin exchange price feed.
    """
    def __init__(self, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize the KuCoin Oracle Feed.

        Args:
            trading_pairs: List of trading pairs, e.g., ["XCH-USDT"]
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds (time before prices are considered valid)
            min_notional: Minimum notional value for trades
        """
        super().__init__("KuCoin", trading_pairs, window_sec, startup_window_sec, min_notional)

    async def _create_websocket_task(self):
        """Create the KuCoin WebSocket task."""
        await kucoin_ws(self.oracle, self)


async def main():
    # Example usage with XCH pairs
    trading_pairs = ["XCH-USDT"]

    async with KucoinOracleFeed(trading_pairs) as feed:
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
