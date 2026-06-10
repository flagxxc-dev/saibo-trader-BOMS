"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  EDGE DETECTOR — LATENCY ARBITRAGE SIGNAL ENGINE                             ║
║                                                                              ║
║  Strategy: BTC moves on Binance → Polymarket lags ~2.7 seconds →            ║
║  Bot detects mispriced market → Kelly-sized position → Market corrects.     ║
║                                                                              ║
║  Target markets (configurable via TRADE_WINDOW_MINUTES):                    ║
║    5  → "Bitcoin Up or Down - 5 Minutes"  (default, highest volume)         ║
║    15 → "Bitcoin Up or Down - 15 Minutes" (lower noise, longer hold)        ║
║  Resolution:    Chainlink BTC/USD oracle at window close                    ║
║  Structure:     Binary UP/DOWN, new market every N minutes                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Update log (v2 — BTC 5-minute up/down market targeting):

  The original detector used a generic BTC market search. This version is
  specifically tuned for the "Bitcoin Up or Down - 5 Minutes" market series,
  which is the highest-volume, most liquid BTC latency-arb target on Polymarket.

  Key changes:
    1. MARKET FILTER: Targets "btc-updown-5m" slug pattern and "up or down"
       question text. Skips all other BTC markets.

    2. TIME-AWARE FAIR VALUE: The 5-minute market has a "price to beat" (the
       BTC price at window open). Fair value is computed as:
         P(UP) = sigmoid( (btc_now - price_to_beat) / scale )
       where scale shrinks as the window progresses (less time = more certain).
       This is more accurate than the generic logistic model.

    3. WINDOW TIMING: Detects how many seconds remain in the 5-minute window.
       Avoids trading in the last 30 seconds (too late to exit) and the first
       5 seconds (price-to-beat not yet stable).

    4. SLUG-BASED MARKET DISCOVERY: Queries Gamma API with the exact slug
       pattern for the 5-minute up/down series, not a generic crypto tag.

    5. CHAINLINK LAG AWARENESS: Chainlink oracle updates every ~2-3 seconds.
       The lag_window_seconds default is kept at 2.7s to match this.
"""

import math
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional

from core.binance_ws import BinanceWebSocketFeed
from core.market_utils import get_seconds_remaining as _get_secs_remaining
from core.polymarket_client import MarketInfo, PolymarketClient
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeSignal:
    """
    A confirmed arbitrage signal ready for execution.

    Attributes:
        market: The Polymarket market to trade.
        token_id: The specific outcome token to buy (UP or DOWN).
        side: "BUY" (always buying the winning outcome).
        current_polymarket_price: Price on Polymarket right now (0.0–1.0).
        fair_value_estimate: Our estimated true probability based on Binance move.
        edge: Probability advantage (fair_value - current_price).
        btc_price_now: Current BTC price on Binance.
        btc_price_lag: BTC price ~2.7 seconds ago (what Polymarket is priced at).
        btc_move: Absolute price change in USD.
        direction: "UP" or "DOWN" — direction of BTC move.
        timestamp: Unix timestamp when signal was generated.
        confidence: Signal confidence score (0.0–1.0).
        seconds_remaining: Seconds left in the 5-minute window.
        price_to_beat: BTC price at window open (resolution reference).
    """
    market: MarketInfo
    token_id: str
    side: str
    current_polymarket_price: float
    fair_value_estimate: float
    edge: float
    asset: str          # "btc", "eth", or "sol"
    price_now: float    # current asset price on Binance
    price_lag: float    # asset price lag_window_seconds ago
    btc_move: float     # kept for compat — same as price_now - price_lag
    direction: str
    timestamp: float
    confidence: float = 0.0
    seconds_remaining: float = 0.0
    price_to_beat: Optional[float] = None

    # Legacy aliases
    @property
    def btc_price_now(self) -> float:
        return self.price_now

    @property
    def btc_price_lag(self) -> float:
        return self.price_lag

    def __str__(self) -> str:
        ptb = f" | PTB: ${self.price_to_beat:,.2f}" if self.price_to_beat else ""
        return (
            f"TradeSignal | {self.asset.upper()} {self.direction} | Edge: {self.edge:.3f} | "
            f"PM: {self.current_polymarket_price:.3f} → Fair: {self.fair_value_estimate:.3f} | "
            f"{self.asset.upper()}: ${self.price_lag:,.2f} → ${self.price_now:,.2f} "
            f"(Δ${self.btc_move:+.2f}){ptb} | {self.seconds_remaining:.0f}s left"
        )


class EdgeDetector:
    """
    Monitors Binance price feeds and Polymarket order books to identify
    latency arbitrage opportunities in {asset}-updown-5m markets.

    Supports BTC, ETH, and SOL simultaneously. Each asset has its own
    Binance feed, fair-value scale, and price-move threshold.
    """

    # Per-asset configuration: slug prefix, sigmoid scale, min USD move
    ASSET_CONFIG: Dict[str, Dict] = {
        "btc": {"base_scale": 150.0, "min_scale": 20.0, "min_price_move": 5.0},
        "eth": {"base_scale": 10.0,  "min_scale": 1.5,  "min_price_move": 0.53},
        "sol": {"base_scale": 1.5,   "min_scale": 0.2,  "min_price_move": 0.05},
        "xrp": {"base_scale": 0.3,   "min_scale": 0.05, "min_price_move": 0.01},
    }

    # Question text keywords — "up or down" matches BTC/ETH/SOL
    MARKET_QUESTION_KEYWORDS = ["up or down", "updown"]

    # Hard floor: never enter with fewer than this many seconds remaining.
    # Overridden at runtime by the configurable min_seconds_remaining param.
    _HARD_MIN_SECONDS = 20

    def __init__(
        self,
        feeds: Dict[str, BinanceWebSocketFeed],
        polymarket_client: PolymarketClient,
        min_edge_threshold: float = 0.04,
        lag_window_seconds: float = 2.7,
        cooldown_seconds: float = 5.0,
        min_market_liquidity: float = 1_000.0,
        trade_window_minutes: int = 5,
        min_entry_price: float = 0.38,
        max_entry_price: float = 0.62,
        min_fair_value_strength: float = 0.05,
        min_seconds_remaining: float = 60.0,
        # Legacy single-feed compat
        binance_feed: Optional[BinanceWebSocketFeed] = None,
    ) -> None:
        # Support old-style single feed: EdgeDetector(binance_feed=..., ...)
        if binance_feed is not None and not feeds:
            feeds = {"btc": binance_feed}
        self.feeds: Dict[str, BinanceWebSocketFeed] = feeds or {}
        self.polymarket_client = polymarket_client
        self.min_edge_threshold = min_edge_threshold
        self.lag_window_seconds = lag_window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.min_market_liquidity = min_market_liquidity
        self.trade_window_minutes: int = trade_window_minutes
        self.min_entry_price = min_entry_price
        self.max_entry_price = max_entry_price
        self.min_fair_value_strength = min_fair_value_strength
        self.min_seconds_remaining = max(self._HARD_MIN_SECONDS, min_seconds_remaining)

        self._last_signal_time: Dict[str, float] = {}  # per-asset cooldown timer
        self._signals_generated: int = 0
        self._evaluations: int = 0

        # Per-asset market cache: asset → Optional[MarketInfo]
        self._active_market_by_asset: Dict[str, Optional[MarketInfo]] = {}
        self._active_market_fetched_at: Dict[str, float] = {}
        self._active_market_ttl: float = 10.0

        # Backward compat: expose single binance_feed for BTC
        self.binance_feed = self.feeds.get("btc")

    # ─────────────────────────────────────────────────────────────────────────
    # Window helpers

    @property
    def _window_seconds(self) -> float:
        """Total window duration in seconds (300 for 5-min, 900 for 15-min)."""
        return float(self.trade_window_minutes * 60)

    @property
    def _max_seconds_remaining(self) -> float:
        """Upper guard: skip evaluation right at window open (price not yet stable)."""
        return self._window_seconds - 1.0

    # ─────────────────────────────────────────────────────────────────────────
    # Compat: expose _active_market as BTC's active market
    @property
    def _active_market(self) -> Optional[MarketInfo]:
        return self._active_market_by_asset.get("btc")

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def evaluate(self) -> Optional[TradeSignal]:
        """
        Run a single evaluation cycle across all configured assets.

        Returns:
            The TradeSignal with highest edge across all assets, or None.
        """
        self._evaluations += 1

        # 1. Evaluate each asset — skip assets still in per-asset cooldown
        now = time.time()
        best_signal: Optional[TradeSignal] = None
        for asset, feed in self.feeds.items():
            if now - self._last_signal_time.get(asset, 0.0) < self.cooldown_seconds:
                continue  # this asset is still cooling down
            signal = await self._evaluate_asset(asset, feed)
            if signal is None:
                continue
            if best_signal is None or signal.edge > best_signal.edge:
                best_signal = signal

        if best_signal:
            self._last_signal_time[best_signal.asset] = time.time()
            self._signals_generated += 1
            logger.info("EDGE DETECTED [#%d]: %s", self._signals_generated, best_signal)

        return best_signal

    async def _evaluate_asset(
        self, asset: str, feed: BinanceWebSocketFeed
    ) -> Optional["TradeSignal"]:
        """Evaluate a single asset for an edge signal."""
        # Get current and lagged price
        price_now = feed.latest_price
        if price_now is None:
            return None

        price_lag = feed.get_price_at(self.lag_window_seconds)
        if price_lag is None:
            return None

        move = price_now - price_lag
        if move == 0:
            return None

        # Minimum price move filter — ignore noise below asset threshold
        cfg = self.ASSET_CONFIG.get(asset, self.ASSET_CONFIG["btc"])
        min_move = cfg.get("min_price_move", 5.0)
        if abs(move) < min_move:
            return None

        direction = "UP" if move > 0 else "DOWN"

        # Get active market for this asset
        market = await self._get_active_5m_market(asset)
        if market is None:
            return None

        # Check timing window
        seconds_remaining = self._get_seconds_remaining(market)
        if seconds_remaining is None:
            return None
        if seconds_remaining < self.min_seconds_remaining:
            return None
        if seconds_remaining > self._max_seconds_remaining:
            return None

        # Resolve the ACTUAL price-to-beat from Binance history.
        # The price-to-beat is the asset price at window-open time, which the
        # market resolves against. Using the real historical price avoids the
        # catastrophic extrapolation bug (fake edge from linear scaling).
        window_elapsed = self._window_seconds - seconds_remaining
        actual_ptb = feed.get_price_at(window_elapsed) if window_elapsed > 1.0 else price_now

        if actual_ptb is None:
            # History buffer too short to find window-open price.
            # Using price_now as fallback would give sigmoid(0) = 0.50, creating
            # ~48% fake edge on any token priced at 2¢ — a catastrophic false signal.
            # Skip this asset until the buffer has enough history.
            logger.debug(
                "[%s] Skipping: price_to_beat unavailable (window_elapsed=%.0fs, "
                "history covers <%.0fs). Wait for feed to accumulate history.",
                asset.upper(), window_elapsed, window_elapsed,
            )
            return None

        return await self._evaluate_5m_market(
            asset, market, price_now, price_lag, move, direction,
            seconds_remaining, actual_ptb,
        )

    def get_stats(self) -> dict:
        """Return evaluation statistics."""
        active_markets = {
            asset: m.question[:50]
            for asset, m in self._active_market_by_asset.items()
            if m is not None
        }
        return {
            "evaluations": self._evaluations,
            "signals_generated": self._signals_generated,
            "signal_rate": (
                self._signals_generated / self._evaluations
                if self._evaluations > 0 else 0.0
            ),
            "last_signal_age_s": (
                round(time.time() - max(self._last_signal_time.values()), 1)
                if self._last_signal_time else None
            ),
            "active_markets": active_markets,
            "assets_monitored": list(self.feeds.keys()),
        }

    def reset_cooldown(self, asset: str) -> None:
        """Restart the per-asset cooldown timer from now.

        Call this when a position for `asset` closes so that the bot
        waits a full cooldown_seconds before re-entering that asset,
        regardless of when the original entry signal fired.
        """
        self._last_signal_time[asset] = time.time()
        logger.debug(
            "[%s] Cooldown reset — next entry in %.0fs",
            asset.upper(), self.cooldown_seconds,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Methods
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_active_5m_market(self, asset: str = "btc") -> Optional[MarketInfo]:
        """
        Return the currently active up/down market for the given asset and window.
        Uses a 10s TTL cache per asset.
        """
        now = time.time()
        cached = self._active_market_by_asset.get(asset)
        last_fetch = self._active_market_fetched_at.get(asset, 0.0)
        if cached is not None and (now - last_fetch) < self._active_market_ttl:
            return cached

        markets = await self.polymarket_client.get_active_markets(
            asset=asset,
            min_liquidity=self.min_market_liquidity,
            force_refresh=True,
        )

        if not markets:
            logger.warning(
                "[%s] get_active_markets returned 0 markets. "
                "Check API connectivity and EDGE_MIN_MARKET_LIQUIDITY=%.0f.",
                asset.upper(), self.min_market_liquidity,
            )
            self._active_market_by_asset[asset] = None
            self._active_market_fetched_at[asset] = now
            return None

        # Filter to the target up/down window markets
        target_markets = [m for m in markets if self._is_target_updown_market(m)]
        if not target_markets:
            logger.warning(
                "[%s] %d market(s) found but NONE passed %dm filter. Questions: %s",
                asset.upper(), len(markets), self.trade_window_minutes,
                [m.question[:50] for m in markets],
            )
            self._active_market_by_asset[asset] = None
            self._active_market_fetched_at[asset] = now
            return None

        tradeable = [
            m for m in target_markets
            if (self._get_seconds_remaining(m) or 0) >= self.min_seconds_remaining
        ]
        if not tradeable:
            self._active_market_by_asset[asset] = None
            self._active_market_fetched_at[asset] = now
            return None

        tradeable.sort(key=lambda m: (
            self._get_seconds_remaining(m) or 999,
            -m.liquidity,
        ))

        chosen = tradeable[0]
        self._active_market_by_asset[asset] = chosen
        self._active_market_fetched_at[asset] = now

        secs_left = self._get_seconds_remaining(chosen) or 0
        logger.debug(
            "[%s] Active %dm market: '%s' | %.0fs left | Liq: $%.0f | UP: %.4f | DOWN: %.4f",
            asset.upper(), self.trade_window_minutes, chosen.question[:55], secs_left,
            chosen.liquidity, chosen.yes_price, chosen.no_price,
        )
        return chosen

    def _is_target_updown_market(self, market: MarketInfo) -> bool:
        """
        Return True if this market matches the configured trade window.

        TRADE_WINDOW_MINUTES=5  → matches "Bitcoin Up or Down - 5 Minutes" series
        TRADE_WINDOW_MINUTES=15 → matches "Bitcoin Up or Down - 15 Minutes" series

        Polymarket title formats observed:
          5-min:  "Bitcoin Up or Down - 5 Minutes"
                  "Bitcoin Up or Down - March 30, 5:25PM-5:30PM ET"
          15-min: "Bitcoin Up or Down - 15 Minutes"
                  "Bitcoin Up or Down - March 30, 3:00PM-3:15PM ET"

        The HH:MM timestamp pattern is identical for both window types, so for
        15-minute detection we parse the actual times and verify the gap is 15
        minutes — not just match the regex blindly.
        """
        q = market.question.lower()
        is_updown = any(kw in q for kw in self.MARKET_QUESTION_KEYWORDS)
        if not is_updown:
            return False

        if self.trade_window_minutes == 5:
            is_5min = (
                "5 min" in q        # "5 minutes", "5 min"
                or "5-min" in q     # "5-minute"
                or "- 5m" in q      # "- 5m"
                or " 5m " in q      # " 5m " (standalone)
                or "5m-" in q       # slug-style
                # Actual Polymarket format: "5:25PM-5:30PM ET" — any HH:MM pair
                # (5-min is the default; we exclude 15-min below)
                or bool(re.search(r"\d{1,2}:\d{2}[ap]m-\d{1,2}:\d{2}[ap]m", q))
            )
            is_15min = "15 min" in q or "15-min" in q
            return is_5min and not is_15min

        # trade_window_minutes == 15
        # Check explicit text keywords first — most reliable.
        is_15min_text = (
            "15 min" in q       # "15 minutes", "15 min"
            or "15-min" in q    # "15-minute"
            or "- 15m" in q     # "- 15m"
            or " 15m " in q     # " 15m " (standalone)
            or "15m-" in q      # slug-style
        )
        # For the HH:MM timestamp format, parse the actual gap to avoid matching
        # 5-minute market titles like "11:35AM-11:40AM" (gap = 5, not 15).
        is_15min_timestamp = self._timestamp_gap_minutes(q) == 15

        is_15min = is_15min_text or is_15min_timestamp
        is_5min_explicit = (
            ("5 min" in q or "5-min" in q or "- 5m" in q or " 5m " in q or "5m-" in q)
            and "15" not in q
        )
        return is_15min and not is_5min_explicit

    @staticmethod
    def _timestamp_gap_minutes(q: str) -> Optional[int]:
        """
        Parse a "HH:MMam-HH:MMpm" substring and return the gap in minutes,
        or None if no such pattern is found.

        Handles hour rollover (e.g. "11:45am-12:00pm" → 15) and AM/PM
        transitions correctly.
        """
        m = re.search(
            r"(\d{1,2}):(\d{2})(am|pm)-(\d{1,2}):(\d{2})(am|pm)", q
        )
        if not m:
            return None

        h1, min1, ap1 = int(m.group(1)), int(m.group(2)), m.group(3)
        h2, min2, ap2 = int(m.group(4)), int(m.group(5)), m.group(6)

        def to_minutes(h: int, mn: int, ap: str) -> int:
            if ap == "pm" and h != 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            return h * 60 + mn

        start = to_minutes(h1, min1, ap1)
        end   = to_minutes(h2, min2, ap2)
        if end <= start:
            end += 24 * 60  # midnight rollover
        return end - start

    # Backward-compat alias (tests may reference the old name)
    def _is_5m_updown_market(self, market: MarketInfo) -> bool:
        return self._is_target_updown_market(market)

    def _get_seconds_remaining(self, market: MarketInfo) -> Optional[float]:
        """
        Estimate seconds remaining in the current market window.
        Delegates to core.market_utils.get_seconds_remaining — single source of truth
        shared with DumpHedgeDetector.
        """
        return _get_secs_remaining(market, self._window_seconds)

    async def _evaluate_5m_market(
        self,
        asset: str,
        market: MarketInfo,
        price_now: float,
        price_lag: float,
        price_move: float,
        direction: str,
        seconds_remaining: float,
        price_to_beat: float,
    ) -> Optional[TradeSignal]:
        """Evaluate a 5-minute up/down market for an arbitrage edge."""
        cfg = self.ASSET_CONFIG.get(asset, self.ASSET_CONFIG["btc"])

        if direction == "UP":
            token_id = market.yes_token_id
            current_pm_price = market.yes_price
        else:
            token_id = market.no_token_id
            current_pm_price = market.no_price

        # Fetch fresh price (WS cache or REST)
        fresh_price = await self.polymarket_client.get_market_price(token_id, "BUY")
        if fresh_price is not None:
            current_pm_price = fresh_price

        # Entry zone filter — only trade when token is near 50/50.
        # Tokens priced outside [min_entry_price, max_entry_price] reflect
        # accumulated directional evidence that overwhelms our 2.7s lag edge.
        # e.g. a token at 15¢ means BTC trended DOWN for the whole window — don't fight it.
        if not (self.min_entry_price <= current_pm_price <= self.max_entry_price):
            logger.debug(
                "[%s] Skip: token price %.3f outside entry zone [%.2f, %.2f]",
                asset.upper(), current_pm_price, self.min_entry_price, self.max_entry_price,
            )
            return None

        # price_to_beat is passed in as the actual Binance price at window open
        # (from feed.get_price_at(window_elapsed)) — no longer extrapolated.

        fair_value = self._compute_fair_value_5m(
            price_now=price_now,
            price_to_beat=price_to_beat,
            seconds_remaining=seconds_remaining,
            direction=direction,
            base_scale=cfg["base_scale"],
            min_scale=cfg["min_scale"],
        )

        # Require genuine directional conviction from the Binance model.
        # If fair_value ≈ 0.50, the signal is noise (price_now ≈ PTB → sigmoid ≈ 0.50).
        # Apparent edge against a cheap token (e.g. 0.50 vs 0.39 = 11%) would be fake —
        # the market priced it at 0.39 for a reason we don't see in a 2.7s window.
        if abs(fair_value - 0.5) < self.min_fair_value_strength:
            logger.debug(
                "[%s] Skip: fair_value=%.3f too close to 0.5 (strength=%.3f < %.3f). "
                "PTB signal too weak.",
                asset.upper(), fair_value, abs(fair_value - 0.5), self.min_fair_value_strength,
            )
            return None

        edge = fair_value - current_pm_price
        if edge < self.min_edge_threshold:
            return None

        time_factor = max(0.1, 1.0 - (seconds_remaining / self._window_seconds))
        norm_move = abs(price_move) / cfg["base_scale"]  # normalised to asset scale
        confidence = min(1.0, (edge / 0.15) * norm_move * (1.0 + time_factor))

        return TradeSignal(
            market=market,
            token_id=token_id,
            side="BUY",
            current_polymarket_price=current_pm_price,
            fair_value_estimate=fair_value,
            edge=edge,
            asset=asset,
            price_now=price_now,
            price_lag=price_lag,
            btc_move=price_move,
            direction=direction,
            timestamp=time.time(),
            confidence=confidence,
            seconds_remaining=seconds_remaining,
            price_to_beat=price_to_beat,
        )

    def _compute_fair_value_5m(
        self,
        price_now: float,
        price_to_beat: float,
        seconds_remaining: float,
        direction: str,
        base_scale: float,
        min_scale: float,
    ) -> float:
        """
        Compute fair probability using a time-aware sigmoid model.

        P(UP) = sigmoid( (price_now - price_to_beat) / scale(t) )
        scale(t) = base_scale * sqrt(t / window) + min_scale

        Dividing by window (300 or 900) normalises t_frac to [0,1] so the
        same base_scale values work correctly for both 5-min and 15-min markets.
        base_scale is asset-specific (BTC=150, ETH=10, SOL=1.5, XRP=0.3).
        """
        t_frac = max(0.01, seconds_remaining / self._window_seconds)
        scale = base_scale * math.sqrt(t_frac) + min_scale

        distance = (price_now - price_to_beat) / scale
        p_up = 1.0 / (1.0 + math.exp(-distance))

        fair_value = p_up if direction == "UP" else (1.0 - p_up)
        return max(0.01, min(0.99, fair_value))
