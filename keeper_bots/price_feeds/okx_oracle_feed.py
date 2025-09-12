import asyncio
import json
import random
import time
import statistics
import aiohttp
import math
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"

# Coinbase for USDT-USD conversion
# Using Coinbase v2 public API for spot prices, as v3 requires authentication
COINBASE_API_URL = "https://api.coinbase.com/v2"
USDT_USD_PAIR = "USDT-USD"


class Oracle:
    def __init__(self, trading_pairs, window_sec=5, startup_window_sec=900, min_notional=10):
        """
        Initialize Oracle with configurable trading pairs.
        
        Args:
            trading_pairs: List of trading pairs, e.g., ["XCH-USDT", "XCH-USD"]
            window_sec: Time window for trades in seconds
            startup_window_sec: Startup window length in seconds (time before prices are considered valid)
            min_notional: Minimum notional value for trades
        """
        self.trading_pairs = trading_pairs
        self.window = window_sec
        self.startup_window = startup_window_sec
        self.min_notional = min_notional
        self.trades = []  # list of (ts_ms, px, qty)
        self.last_pub = 0
        self.last_trade_ts = 0
        self.last_price = float("nan")
        self.usdt_usd_price = float("nan")
        self.start_time = None  # Will be set when first connected
        
        # Create subscription channels based on provided trading pairs
        self.trade_ch = {"op": "subscribe", "args": [{"channel": "trades", "instId": p} for p in trading_pairs]}
        self.book_ch = {"op": "subscribe", "args": [{"channel": "books5", "instId": p} for p in trading_pairs]}
        
        # Extract base currency for logging (assumes all pairs have same base)
        self.base_currency = trading_pairs[0].split("-")[0] if trading_pairs else "UNKNOWN"

    def set_usdt_usd_price(self, price):
        if price and price > 0:
            self.usdt_usd_price = price

    def update_parameters(self, window_sec=None, startup_window_sec=None, min_notional=None):
        """
        Update Oracle parameters dynamically.
        
        Args:
            window_sec: New time window for trades in seconds (optional)
            startup_window_sec: New startup window length in seconds (optional)
            min_notional: New minimum notional value for trades (optional)
        """
        # Validate parameters
        if window_sec is not None:
            if not isinstance(window_sec, (int, float)) or window_sec <= 0:
                raise ValueError(f"window_sec must be a positive number, got: {window_sec}")
            
            old_window = self.window
            self.window = window_sec
            
            # Clean up trades outside the new window if it's smaller
            if window_sec < old_window:
                now = int(time.time() * 1000)
                cutoff = now - self.window * 1000
                # Remove trades older than new window
                original_count = len(self.trades)
                self.trades = [trade for trade in self.trades if trade[0] >= cutoff]
                removed_count = original_count - len(self.trades)
                if removed_count > 0:
                    logging.info("Window reduced from %ds to %ds, removed %d old trades", 
                               old_window, window_sec, removed_count)
        
        if startup_window_sec is not None:
            if not isinstance(startup_window_sec, (int, float)) or startup_window_sec < 0:
                raise ValueError(f"startup_window_sec must be a non-negative number, got: {startup_window_sec}")
            self.startup_window = startup_window_sec
        
        if min_notional is not None:
            if not isinstance(min_notional, (int, float)) or min_notional < 0:
                raise ValueError(f"min_notional must be a non-negative number, got: {min_notional}")
            self.min_notional = min_notional

    def add_trade(self, instId, ts_ms, px, qty):
        # Convert price to USD if necessary
        usd_px = px
        if instId.endswith("-USDT"):
            if not math.isnan(self.usdt_usd_price):
                usd_px = px * self.usdt_usd_price
            else:
                # Can't convert, so we skip this trade
                return

        if usd_px * qty < self.min_notional:
            return
        now = int(time.time() * 1000)
        self.trades.append((ts_ms, usd_px, qty))
        # drop old
        cutoff = now - self.window * 1000
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.pop(0)
        self.last_trade_ts = ts_ms

    def compute(self, fallback_mid=None):
        now = int(time.time() * 1000)
        
        # Check if we're still in startup window
        if self.start_time is not None:
            startup_elapsed = (now / 1000.0) - self.start_time
            if startup_elapsed < self.startup_window:
                # Still in startup window - return NaN
                meta = {"stale": False, "window": self.window, "trades": len(self.trades), "startup": True}
                return float("nan"), meta
        
        # stale?
        stale = (now - self.last_trade_ts) > 5000
        price = None
        meta = {"stale": stale, "window": self.window, "trades": len(self.trades), "startup": False}
        if self.trades:
            vol = sum(q for _, _, q in self.trades)
            if vol > 0:
                vwap = sum(px * q for _, px, q in self.trades) / vol
                # outlier trim (simple): drop if far from median of trade prices
                med = statistics.median([px for _, px, _ in self.trades])
                if abs(vwap - med) / med > 0.03 and len(self.trades) > 4:
                    # trim extremes
                    prices = sorted([px for _, px, _ in self.trades])
                    core = prices[len(prices) // 10 : -len(prices) // 10 or None]
                    vwap = sum(p * q for (_, p, q) in self.trades if p in core) / sum(
                        q for (_, p, q) in self.trades if p in core
                    )
                price = vwap
        if price is None:
            price = fallback_mid if fallback_mid is not None else self.last_price
            meta["degraded"] = True
        self.last_price = price
        meta["ts"] = now
        return price, meta


async def usdt_price_fetcher(oracle: Oracle):
    """Fetches USDT-USD price from Coinbase and updates the oracle."""
    base_delay = 5.0
    max_delay = 60.0
    delay = base_delay

    while True:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{COINBASE_API_URL}/prices/{USDT_USD_PAIR}/spot"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        price = float(data["data"]["amount"])
                        oracle.set_usdt_usd_price(price)
                        delay = base_delay  # Reset backoff delay on success
                        await asyncio.sleep(15)  # Wait for the normal interval
                        continue

                    # Handle non-200 server responses
                    body = await response.text()
                    logging.warning(
                        "Failed to fetch USDT-USD price from Coinbase (Status: %s). Retrying in %.2fs. Body: %s",
                        response.status,
                        delay,
                        body,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.error("Error fetching USDT-USD price: %s. Retrying in %.2fs.", e, delay)

        await asyncio.sleep(delay)
        delay = min(max_delay, delay * 1.5 + random.uniform(0, 1))  # Exponential backoff with jitter


async def okx_ws(oracle: Oracle, feed_instance=None):
    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(OKX_WS, heartbeat=15) as ws:
                    await ws.send_json(oracle.trade_ch)
                    await ws.send_json(oracle.book_ch)
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


class OkxOracleFeed:
    """
    A wrapper class that provides a similar interface to OkxFeed for integration 
    with existing announcer bot code.
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
        self.oracle = Oracle(trading_pairs, window_sec, startup_window_sec, min_notional)
        self._tasks = []
        self.book_mids = {}  # Store current book mid prices for fallback
        
    async def __aenter__(self):
        """Start the feed tasks."""
        logging.info("Starting OKX Oracle Feed...")
        self._tasks = [
            asyncio.create_task(okx_ws(self.oracle, self)),
            asyncio.create_task(usdt_price_fetcher(self.oracle))
        ]
        # Give it a moment to connect and start receiving data
        await asyncio.sleep(2)
        return self
        
    async def __aexit__(self, exc_type, exc_value, traceback):
        """Stop the feed tasks."""
        logging.info("Stopping OKX Oracle Feed...")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        
    async def get_price(self):
        """Get the current price from the oracle."""
        # Calculate volume-weighted fallback mid-price from current orderbook data
        fallback_mid = None
        if self.book_mids:
            # Calculate a volume-weighted mid-price for fallback
            total_book_volume = sum(vol for _, vol in self.book_mids.values())
            if total_book_volume > 0:
                weighted_sum = sum(px * vol for px, vol in self.book_mids.values())
                fallback_mid = weighted_sum / total_book_volume
            else:
                # Fallback to simple average if no volume
                prices = [px for px, _ in self.book_mids.values()]
                if prices:
                    fallback_mid = statistics.mean(prices)
        
        price, meta = self.oracle.compute(fallback_mid=fallback_mid)
        return price

    def update_parameters(self, window_sec=None, startup_window_sec=None, min_notional=None):
        """
        Update feed parameters dynamically.
        
        Args:
            window_sec: New time window for trades in seconds (optional)
            startup_window_sec: New startup window length in seconds (optional)
            min_notional: New minimum notional value for trades (optional)
        """
        self.oracle.update_parameters(window_sec, startup_window_sec, min_notional)
        logging.info("Updated OkxOracleFeed parameters: window_sec=%s, startup_window_sec=%s, min_notional=%s",
                    self.oracle.window, self.oracle.startup_window, self.oracle.min_notional)


async def main():
    # Example usage with XCH pairs
    trading_pairs = ["XCH-USDT", "XCH-USD"]
    oracle = Oracle(trading_pairs)
    
    # Run the core oracle tasks
    core_tasks = asyncio.gather(okx_ws(oracle), usdt_price_fetcher(oracle))
    await core_tasks


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nExiting.")