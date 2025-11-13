import math
import logging
import statistics
from collections import deque
import time

log = logging.getLogger("price_aggregator")


class PriceAggregator:
    """
    Aggregates prices from multiple oracle feeds using volume-weighted average.
    Includes protection against volume manipulation attacks.
    """

    def __init__(
        self,
        feeds,
        min_valid_feeds=2,
        aggregation_method="volume_weighted",
        max_single_feed_weight=0.6,
        volume_spike_threshold=3.0,
        volume_history_length=20,
        price_deviation_threshold=0.05
    ):
        """
        Initialize the price aggregator with manipulation protection.

        Args:
            feeds: Dictionary of feed_name -> feed_instance pairs
            min_valid_feeds: Minimum number of feeds that must provide valid prices (default: 2)
            aggregation_method: "volume_weighted", "median", or "simple_average"
            max_single_feed_weight: Maximum weight any single feed can have (0.6 = 60%)
            volume_spike_threshold: Volume spike multiplier to trigger capping (3.0 = 3x normal)
            volume_history_length: Number of historical volume samples to track
            price_deviation_threshold: Max price deviation from median before flagging (5%)
        """
        self.feeds = feeds
        self.min_valid_feeds = min_valid_feeds
        self.aggregation_method = aggregation_method
        self.max_single_feed_weight = max_single_feed_weight
        self.volume_spike_threshold = volume_spike_threshold
        self.price_deviation_threshold = price_deviation_threshold

        # Track volume history for each feed
        self.volume_history = {feed_name: deque(maxlen=volume_history_length) for feed_name in feeds.keys()}

        if len(feeds) < min_valid_feeds:
            raise ValueError(
                f"Number of feeds ({len(feeds)}) is less than min_valid_feeds ({min_valid_feeds})"
            )

    async def get_aggregated_price(self):
        """
        Get aggregated price from all feeds with manipulation protection.

        Returns:
            Aggregated price (float), or NaN if minimum valid feeds requirement is not met
        """
        valid_prices = []

        # Collect prices from all feeds
        for feed_name, feed in self.feeds.items():
            try:
                price, meta = await feed.get_price()

                # Check if price is valid
                if price is None or math.isnan(price):
                    log.debug("Feed %s returned invalid price (NaN or None)", feed_name)
                    continue

                # Get trade count for volume weighting
                trade_count = meta.get("trades", 0) if meta else 0

                valid_prices.append({
                    "feed": feed_name,
                    "price": price,
                    "trades": trade_count,
                    "meta": meta
                })

                # Update volume history
                self.volume_history[feed_name].append(trade_count)

                log.debug(
                    "Feed %s: price=%.2f, trades=%d, meta=%s",
                    feed_name, price, trade_count, meta
                )

            except Exception as err:
                log.error("Failed to get price from feed %s: %s", feed_name, err)
                continue

        # Check if we have enough valid feeds
        if len(valid_prices) < self.min_valid_feeds:
            log.warning(
                "Insufficient valid feeds: %d/%d (minimum required: %d)",
                len(valid_prices),
                len(self.feeds),
                self.min_valid_feeds
            )
            return float("nan")

        # Apply manipulation protections
        valid_prices = self._apply_manipulation_protections(valid_prices)

        # Aggregate prices based on selected method
        if self.aggregation_method == "volume_weighted":
            aggregated_price = self._volume_weighted_average(valid_prices)
        elif self.aggregation_method == "median":
            aggregated_price = self._median_price(valid_prices)
        elif self.aggregation_method == "simple_average":
            aggregated_price = self._simple_average(valid_prices)
        else:
            raise ValueError(f"Unknown aggregation method: {self.aggregation_method}")

        log.info(
            "Aggregated price (method=%s): %.2f from %d feeds: %s",
            self.aggregation_method,
            aggregated_price,
            len(valid_prices),
            ", ".join([f"{p['feed']}=${p['price']:.2f}(trades={p['trades']},weight={p.get('adjusted_weight', 0):.2%})" for p in valid_prices])
        )

        return aggregated_price

    def _apply_manipulation_protections(self, valid_prices):
        """
        Apply various manipulation protection mechanisms.

        Args:
            valid_prices: List of price data from feeds

        Returns:
            Modified list of prices with adjusted weights
        """
        if len(valid_prices) < 2:
            return valid_prices

        # 1. Detect price outliers
        prices_only = [p["price"] for p in valid_prices]
        median_price = statistics.median(prices_only)

        for price_data in valid_prices:
            deviation = abs(price_data["price"] - median_price) / median_price
            if deviation > self.price_deviation_threshold:
                log.warning(
                    "Feed %s has price %.2f deviating %.2f%% from median %.2f (threshold: %.2f%%)",
                    price_data["feed"],
                    price_data["price"],
                    deviation * 100,
                    median_price,
                    self.price_deviation_threshold * 100
                )

        # 2. Detect and cap volume spikes
        for price_data in valid_prices:
            feed_name = price_data["feed"]
            current_trades = price_data["trades"]

            # Calculate average historical volume
            if len(self.volume_history[feed_name]) > 3:
                avg_volume = statistics.mean(list(self.volume_history[feed_name])[:-1])  # Exclude current
                if avg_volume > 0 and current_trades > avg_volume * self.volume_spike_threshold:
                    # Volume spike detected - cap the volume
                    capped_trades = int(avg_volume * self.volume_spike_threshold)
                    log.warning(
                        "Feed %s volume spike detected: %d trades (avg: %.1f, threshold: %.1fx). Capping to %d",
                        feed_name,
                        current_trades,
                        avg_volume,
                        self.volume_spike_threshold,
                        capped_trades
                    )
                    price_data["trades"] = capped_trades
                    price_data["volume_capped"] = True

        # 3. Cap maximum weight any single feed can have
        total_trades = sum(p["trades"] for p in valid_prices)
        if total_trades > 0:
            for price_data in valid_prices:
                natural_weight = price_data["trades"] / total_trades
                if natural_weight > self.max_single_feed_weight:
                    # Cap the weight
                    max_trades = int(total_trades * self.max_single_feed_weight)
                    log.warning(
                        "Feed %s weight capped: %.2f%% -> %.2f%% (trades: %d -> %d)",
                        price_data["feed"],
                        natural_weight * 100,
                        self.max_single_feed_weight * 100,
                        price_data["trades"],
                        max_trades
                    )
                    price_data["trades"] = max_trades
                    price_data["weight_capped"] = True

                # Store adjusted weight for logging
                price_data["adjusted_weight"] = price_data["trades"] / sum(p["trades"] for p in valid_prices)

        return valid_prices

    def _volume_weighted_average(self, valid_prices):
        """Calculate volume-weighted average based on trade counts."""
        total_trades = sum(p["trades"] for p in valid_prices)

        if total_trades == 0:
            # If no trades in any feed, fall back to simple average
            log.warning("No trades in any feed, falling back to simple average")
            return self._simple_average(valid_prices)

        weighted_sum = sum(p["price"] * p["trades"] for p in valid_prices)
        return weighted_sum / total_trades

    def _median_price(self, valid_prices):
        """Calculate median price."""
        prices = [p["price"] for p in valid_prices]
        return statistics.median(prices)

    def _simple_average(self, valid_prices):
        """Calculate simple average of prices."""
        prices = [p["price"] for p in valid_prices]
        return sum(prices) / len(prices)

    def get_feed_names(self):
        """Get list of feed names."""
        return list(self.feeds.keys())

    def get_feed_count(self):
        """Get total number of feeds."""
        return len(self.feeds)


# Example usage
async def example():
    """Example of how to use PriceAggregator."""
    from keeper_bots.price_feeds.okx_oracle_feed import OkxOracleFeed
    from keeper_bots.price_feeds.gate_oracle_feed import GateOracleFeed
    from keeper_bots.price_feeds.kucoin_oracle_feed import KucoinOracleFeed

    # Initialize feeds
    # Note: Gate.io uses underscore, KuCoin and OKX use dash
    okx_feed = OkxOracleFeed(
        trading_pairs=["XCH-USDT"],
        window_sec=5,
        startup_window_sec=900,
        min_notional=10
    )

    gate_feed = GateOracleFeed(
        trading_pairs=["XCH_USDT"],
        window_sec=5,
        startup_window_sec=900,
        min_notional=10
    )

    kucoin_feed = KucoinOracleFeed(
        trading_pairs=["XCH-USDT"],
        window_sec=5,
        startup_window_sec=900,
        min_notional=10
    )

    # Create aggregator with manipulation protection
    feeds = {
        "OKX": okx_feed,
        "Gate.io": gate_feed,
        "KuCoin": kucoin_feed
    }

    aggregator = PriceAggregator(
        feeds=feeds,
        min_valid_feeds=2,
        aggregation_method="volume_weighted",
        max_single_feed_weight=0.6,  # No exchange can have >60% weight
        volume_spike_threshold=3.0,   # Cap volume if >3x normal
        price_deviation_threshold=0.05  # Flag if price deviates >5% from median
    )

    # Start feeds
    async with okx_feed, gate_feed, kucoin_feed:
        # Get aggregated price
        price = await aggregator.get_aggregated_price()
        print(f"Aggregated price: {price:.2f}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example())
