"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  DUMP-HEDGE DETECTOR — STRUCTURAL ARBITRAGE ENGINE                           ║
║                                                                              ║
║  Strategy: UP + DOWN tokens always resolve to $1.00 combined.               ║
║  When the market temporarily prices them below $1.00 total, buying both     ║
║  sides locks in a guaranteed profit regardless of direction.                 ║
║                                                                              ║
║  Entry condition: up_price + down_price <= SUM_TARGET (default 0.95)        ║
║  Profit per share pair: 1.00 - combined_price (locked at entry)             ║
║                                                                              ║
║  No Binance data needed. No directional prediction needed.                  ║
║  Pure structural edge from liquidity imbalances in the order book.          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.market_utils import get_seconds_remaining
from core.polymarket_client import MarketInfo, PolymarketClient
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DumpHedgeSignal:
    """
    A confirmed dump-hedge arbitrage opportunity.

    Both YES and NO token IDs are included — caller buys BOTH simultaneously.
    Profit is locked at entry: (1.0 - combined_price) * size_shares USDC.
    """
    market: MarketInfo
    asset: str
    yes_token_id: str
    no_token_id: str
    yes_price: float            # current BUY ask for YES (UP) token
    no_price: float             # current BUY ask for NO (DOWN) token
    combined_price: float       # yes_price + no_price
    discount: float             # 1.0 - combined_price (locked profit per share)
    discount_pct: float         # discount / combined_price (ROI at entry)
    seconds_remaining: float    # seconds left in the market window
    timestamp: float

    def __str__(self) -> str:
        return (
            f"DumpHedge | {self.asset.upper()} | "
            f"YES: {self.yes_price:.3f}  NO: {self.no_price:.3f}  "
            f"Sum: {self.combined_price:.3f} | "
            f"Locked: {self.discount:.3f}/share ({self.discount_pct:.1%} ROI) | "
            f"{self.seconds_remaining:.0f}s left"
        )


class DumpHedgeDetector:
    """
    Scans active Polymarket markets for combined YES+NO prices below
    the sum target, indicating a structural arbitrage opportunity.

    Checks all configured assets once per evaluate() call. Returns
    the signal with the highest locked profit, or None.

    Does NOT require Binance data. Works entirely from Polymarket pricing.
    """

    def __init__(
        self,
        polymarket_client: PolymarketClient,
        assets: List[str],
        sum_target: float = 0.95,
        min_discount: float = 0.03,
        min_market_liquidity: float = 1_000.0,
        trade_window_minutes: int = 5,
        min_seconds_remaining: float = 60.0,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.polymarket_client = polymarket_client
        self.assets = assets
        self.sum_target = sum_target
        self.min_discount = min_discount
        self.min_market_liquidity = min_market_liquidity
        self.trade_window_minutes = trade_window_minutes
        self.min_seconds_remaining = min_seconds_remaining
        self.cooldown_seconds = cooldown_seconds

        self._window_seconds = float(trade_window_minutes * 60)
        self._last_signal_time: Dict[str, float] = {}
        self._signals_generated: int = 0
        self._evaluations: int = 0
        # Mirror of EdgeDetector._active_market_by_asset for dashboard display
        self._active_market_by_asset: Dict[str, object] = {a: None for a in assets}

        logger.info(
            "DumpHedgeDetector initialized | Assets: %s | SumTarget: %.2f | "
            "MinDiscount: %.2f | Window: %dm",
            ", ".join(a.upper() for a in assets),
            sum_target, min_discount, trade_window_minutes,
        )

    async def evaluate(self) -> Optional[DumpHedgeSignal]:
        """
        Scan all configured assets for a dump-hedge opportunity.
        Returns the signal with highest locked discount, or None.
        """
        self._evaluations += 1
        now = time.time()
        best: Optional[DumpHedgeSignal] = None

        for asset in self.assets:
            if now - self._last_signal_time.get(asset, 0.0) < self.cooldown_seconds:
                continue
            signal = await self._evaluate_asset(asset)
            if signal is None:
                continue
            if best is None or signal.discount > best.discount:
                best = signal

        if best:
            self._last_signal_time[best.asset] = time.time()
            self._signals_generated += 1
            logger.info("DUMP-HEDGE DETECTED [#%d]: %s", self._signals_generated, best)

        return best

    async def _evaluate_asset(self, asset: str) -> Optional[DumpHedgeSignal]:
        """
        Check all active markets for an asset and return the one with the
        highest locked discount, or None if no opportunity exists.

        Loops all returned markets instead of only the first so that the bot
        captures the best structural edge when multiple windows are active
        simultaneously (e.g., during a 5-min/15-min transition period).
        """
        markets = await self.polymarket_client.get_active_markets(
            asset=asset,
            min_liquidity=self.min_market_liquidity,
            force_refresh=False,
        )
        if not markets:
            self._active_market_by_asset[asset] = None
            return None

        best: Optional[DumpHedgeSignal] = None

        for market in markets:
            signal = await self._check_market_dh(asset, market)
            if signal is None:
                continue
            if best is None or signal.discount > best.discount:
                best = signal

        # Update dashboard cache with the best market found (or None)
        self._active_market_by_asset[asset] = best.market if best else None
        return best

    async def _check_market_dh(
        self, asset: str, market: object
    ) -> Optional[DumpHedgeSignal]:
        """Evaluate a single market for a dump-hedge opportunity."""
        # Timing check — don't enter too close to expiry
        secs = get_seconds_remaining(market, self._window_seconds)
        if secs is None or secs < self.min_seconds_remaining:
            logger.debug(
                "[%s] DH skip %s: %.0fs remaining < %.0fs minimum",
                asset.upper(), market.question[:40], secs or 0, self.min_seconds_remaining,
            )
            return None

        # Fetch fresh BUY prices for both legs via REST (or WS cache if available).
        # Never fall back to stale discovery-cache prices — they can be minutes old
        # and produce false signals (signal says 0.91, execution sees real 0.99).
        yes_price = await self.polymarket_client.get_market_price(market.yes_token_id, "BUY")
        no_price  = await self.polymarket_client.get_market_price(market.no_token_id,  "BUY")

        if yes_price is None or no_price is None or yes_price <= 0 or no_price <= 0:
            logger.debug(
                "[%s] DH skip %s: price unavailable (yes=%s no=%s)",
                asset.upper(), market.question[:40], yes_price, no_price,
            )
            return None

        combined = yes_price + no_price
        discount = 1.0 - combined

        if combined > self.sum_target:
            logger.debug(
                "[%s] DH: combined=%.4f > target=%.4f — no opportunity",
                asset.upper(), combined, self.sum_target,
            )
            return None

        if discount < self.min_discount:
            logger.debug(
                "[%s] DH: discount=%.4f < min=%.4f — too thin",
                asset.upper(), discount, self.min_discount,
            )
            return None

        return DumpHedgeSignal(
            market=market,
            asset=asset,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            yes_price=yes_price,
            no_price=no_price,
            combined_price=combined,
            discount=discount,
            discount_pct=discount / combined if combined > 0 else 0.0,
            seconds_remaining=secs,
            timestamp=time.time(),
        )

    def reset_cooldown(self, asset: str) -> None:
        """Reset per-asset cooldown (call after a DH position closes)."""
        self._last_signal_time[asset] = time.time()

    def get_stats(self) -> dict:
        return {
            "dh_evaluations": self._evaluations,
            "dh_signals_generated": self._signals_generated,
        }
