"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  KELLY CRITERION POSITION SIZER                                              ║
║                                                                              ║
║  Implements the Kelly Criterion for optimal position sizing in binary        ║
║  prediction markets. Uses fractional Kelly to reduce variance.              ║
║                                                                              ║
║  Kelly Formula (binary bet):                                                 ║
║    f* = (p * b - q) / b                                                      ║
║  Where:                                                                      ║
║    f* = fraction of bankroll to bet                                          ║
║    p  = probability of winning (our fair value estimate)                    ║
║    q  = probability of losing (1 - p)                                       ║
║    b  = net odds received on the bet (payout / stake - 1)                   ║
║                                                                              ║
║  For Polymarket binary markets where you buy at price `c` (0.0–1.0):       ║
║    b = (1 - c) / c  (payout ratio: win (1-c) per unit staked c)            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from dataclasses import dataclass
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class KellyResult:
    """Result of a Kelly position size calculation."""
    kelly_fraction: float       # Raw Kelly fraction (0.0–1.0)
    fractional_kelly: float     # Scaled Kelly fraction (after applying kelly_multiplier)
    position_size_usdc: float   # Recommended position size in USDC
    bankroll: float             # Current bankroll used for calculation
    win_probability: float      # Estimated win probability (p)
    current_price: float        # Current market price (c)
    edge: float                 # Probability edge (p - c)
    net_odds: float             # Net odds (b = (1-c)/c)
    capped: bool = False        # True if position was capped by max_fraction limit

    def __str__(self) -> str:
        return (
            f"Kelly: raw={self.kelly_fraction:.4f} | "
            f"fractional={self.fractional_kelly:.4f} | "
            f"size=${self.position_size_usdc:.2f} USDC | "
            f"edge={self.edge:.4f} | "
            f"{'CAPPED' if self.capped else 'uncapped'}"
        )


class KellySizer:
    """
    Calculates optimal position sizes using the Kelly Criterion.

    Uses fractional Kelly (half-Kelly by default) to reduce variance
    while preserving the majority of the theoretical edge advantage.

    The Kelly Criterion maximises the long-run geometric growth rate of
    the bankroll. Full Kelly is theoretically optimal but produces high
    variance; fractional Kelly (0.25–0.5) is standard practice.
    """

    def __init__(
        self,
        kelly_fraction: float = 0.5,
        max_position_fraction: float = 0.08,
        min_position_usdc: float = 1.0,
        fixed_bet_usdc: float = 0.0,
        adaptive_kelly_enabled: bool = False,
        adaptive_kelly_floor: float = 0.1,
    ) -> None:
        """
        Args:
            kelly_fraction: Multiplier applied to the raw Kelly fraction.
                            0.5 = half-Kelly (recommended for live trading).
                            0.25 = quarter-Kelly (more conservative).
            max_position_fraction: Hard cap on position size as fraction of bankroll.
                                   Overrides Kelly if Kelly suggests a larger bet.
            min_position_usdc: Minimum position size in USDC. Signals below this
                               threshold are skipped.
            fixed_bet_usdc: If > 0, use this exact USDC amount per trade instead of
                            Kelly sizing. Set to 0.0 (default) to use Kelly Criterion.
                            The fixed bet is still subject to max_position_fraction cap.
            adaptive_kelly_enabled: If True, dynamically scale kelly_fraction based on
                            recent win rate — lower when model under-performs, higher
                            when it's consistently right (capped at base kelly_fraction).
            adaptive_kelly_floor: Minimum kelly_fraction multiplier when win rate is poor.
                            E.g. 0.1 = floor at 10% of the base kelly_fraction.
        """
        if not 0.0 < kelly_fraction <= 1.0:
            raise ValueError(f"kelly_fraction must be in (0, 1], got {kelly_fraction}")
        if not 0.0 < max_position_fraction <= 0.50:
            raise ValueError(
                f"max_position_fraction must be in (0, 0.50], got {max_position_fraction}"
            )
        if fixed_bet_usdc < 0:
            raise ValueError(f"fixed_bet_usdc must be >= 0, got {fixed_bet_usdc}")

        self.kelly_fraction = kelly_fraction
        self.max_position_fraction = max_position_fraction
        self.min_position_usdc = min_position_usdc
        self.fixed_bet_usdc = fixed_bet_usdc
        self.adaptive_kelly_enabled = adaptive_kelly_enabled
        self.adaptive_kelly_floor = adaptive_kelly_floor

        if fixed_bet_usdc > 0:
            logger.info(
                "KellySizer: FIXED BET mode active — $%.2f USDC per trade "
                "(Kelly Criterion disabled)",
                fixed_bet_usdc,
            )
        else:
            logger.info(
                "KellySizer: Kelly mode active — %.0f%%-Kelly, max %.0f%% of bankroll%s",
                kelly_fraction * 100,
                max_position_fraction * 100,
                " [adaptive]" if adaptive_kelly_enabled else "",
            )

    def effective_kelly_fraction(self, win_rate: Optional[float] = None) -> float:
        """
        Return the effective Kelly fraction, optionally scaled by recent win rate.

        Adaptive scaling rules (when adaptive_kelly_enabled=True and min 10 trades):
          win_rate < 0.45  → scale down to floor  (model is underperforming)
          0.45 ≤ rate < 0.50 → linear blend floor→base
          0.50 ≤ rate < 0.55 → use base fraction  (neutral)
          rate ≥ 0.55      → scale up to base × 1.25, capped at base (performing well)

        Returns base kelly_fraction when adaptive is disabled or win_rate is None.
        """
        if not self.adaptive_kelly_enabled or win_rate is None:
            return self.kelly_fraction

        floor = self.kelly_fraction * self.adaptive_kelly_floor
        if win_rate < 0.45:
            fraction = floor
        elif win_rate < 0.50:
            # Linear blend: floor at 0.45, base at 0.50
            t = (win_rate - 0.45) / 0.05
            fraction = floor + t * (self.kelly_fraction - floor)
        elif win_rate < 0.55:
            fraction = self.kelly_fraction
        else:
            # Outperforming: allow up to 1.25× base, hard-capped at 1.0 (full Kelly)
            fraction = min(self.kelly_fraction * 1.25, 1.0)

        if fraction != self.kelly_fraction:
            logger.debug(
                "Adaptive Kelly: win_rate=%.1f%% → fraction %.3f (base %.3f)",
                win_rate * 100, fraction, self.kelly_fraction,
            )
        return fraction

    def calculate(
        self,
        bankroll: float,
        win_probability: float,
        current_price: float,
        historical_win_rate: Optional[float] = None,
    ) -> Optional[KellyResult]:
        """
        Calculate the optimal position size for a binary prediction market bet.

        Args:
            bankroll: Current available capital in USDC.
            win_probability: Estimated probability of winning (0.0–1.0).
                             This is our fair value estimate from the edge detector.
            current_price: Current market price to buy at (0.0–1.0).
                           This is what we pay per share.
            historical_win_rate: Optional recent win rate (0.0–1.0) used by
                           adaptive Kelly to scale the fraction up/down.

        Returns:
            KellyResult with position size, or None if the bet has no edge
            or the position would be below the minimum threshold.
        """
        if not (0.0 < win_probability < 1.0):
            logger.warning("Invalid win_probability: %.4f — must be in (0, 1)", win_probability)
            return None

        if not (0.0 < current_price < 1.0):
            logger.warning("Invalid current_price: %.4f — must be in (0, 1)", current_price)
            return None

        if bankroll <= 0:
            logger.warning("Bankroll is zero or negative: %.2f", bankroll)
            return None

        # ── FIXED BET MODE ────────────────────────────────────────────────────
        # When fixed_bet_usdc > 0, bypass Kelly math entirely and use the
        # configured fixed amount. The bet is still capped at max_position_fraction
        # of the current bankroll as a safety guard.
        if self.fixed_bet_usdc > 0:
            max_allowed = bankroll * self.max_position_fraction
            position_size = min(self.fixed_bet_usdc, max_allowed)
            capped = position_size < self.fixed_bet_usdc

            if position_size < self.min_position_usdc:
                logger.debug(
                    "Fixed bet $%.2f below minimum $%.2f — skipping",
                    position_size,
                    self.min_position_usdc,
                )
                return None

            b = (1.0 - current_price) / current_price
            p = win_probability
            q = 1.0 - p
            raw_kelly = (p * b - q) / b  # Computed for logging only
            fractional = position_size / bankroll

            result = KellyResult(
                kelly_fraction=raw_kelly,
                fractional_kelly=fractional,
                position_size_usdc=round(position_size, 2),
                bankroll=bankroll,
                win_probability=p,
                current_price=current_price,
                edge=p - current_price,
                net_odds=b,
                capped=capped,
            )
            logger.debug("Fixed bet result: %s", result)
            return result
        # ── END FIXED BET MODE ────────────────────────────────────────────────

        # Kelly formula for binary prediction market
        # Net odds: if you pay c per share and win, you receive 1.0 per share
        # Net profit per unit staked = (1 - c) / c
        b = (1.0 - current_price) / current_price  # Net odds
        p = win_probability
        q = 1.0 - p

        # Raw Kelly fraction: f* = (p*b - q) / b
        raw_kelly = (p * b - q) / b

        if raw_kelly <= 0:
            logger.debug(
                "No Kelly edge: p=%.4f, price=%.4f, raw_kelly=%.4f",
                p,
                current_price,
                raw_kelly,
            )
            return None

        # Apply fractional Kelly multiplier (adaptive if enabled)
        eff_fraction = self.effective_kelly_fraction(historical_win_rate)
        fractional = raw_kelly * eff_fraction

        # Apply hard cap
        capped = False
        if fractional > self.max_position_fraction:
            logger.debug(
                "Kelly fraction %.4f capped at max %.4f",
                fractional,
                self.max_position_fraction,
            )
            fractional = self.max_position_fraction
            capped = True

        position_size = bankroll * fractional

        if position_size < self.min_position_usdc:
            logger.debug(
                "Position size $%.2f below minimum $%.2f — skipping",
                position_size,
                self.min_position_usdc,
            )
            return None

        result = KellyResult(
            kelly_fraction=raw_kelly,
            fractional_kelly=fractional,
            position_size_usdc=round(position_size, 2),
            bankroll=bankroll,
            win_probability=p,
            current_price=current_price,
            edge=p - current_price,
            net_odds=b,
            capped=capped,
        )

        logger.debug("Kelly calculation: %s", result)
        return result

    def expected_value(self, win_probability: float, current_price: float) -> float:
        """
        Calculate the expected value per dollar staked.

        EV = p * (1/c - 1) - (1-p)
           = p/c - 1

        Args:
            win_probability: Estimated win probability.
            current_price: Current market price.

        Returns:
            Expected value per dollar staked. Positive = profitable bet.
        """
        if current_price <= 0:
            return 0.0
        return (win_probability / current_price) - 1.0
