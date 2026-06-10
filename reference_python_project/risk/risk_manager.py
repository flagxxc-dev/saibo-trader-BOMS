"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  RISK MANAGER — DRAWDOWN LIMITS & KILL SWITCH                                ║
║                                                                              ║
║  Enforces strict capital protection rules:                                   ║
║    • Maximum single position size: 8% of portfolio                          ║
║    • Daily loss limit: -20% → automatic trading halt                        ║
║    • Total drawdown kill switch: -40% → permanent halt until manual reset   ║
║    • Maximum concurrent open positions: 3                                    ║
║                                                                              ║
║  The kill switch is the most important safety feature. If the bot is        ║
║  running while you sleep and something breaks, you want it to stop,         ║
║  not to keep trading.                                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import datetime
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class TradingStatus(Enum):
    """Current operational status of the trading bot."""
    ACTIVE = "ACTIVE"                    # Normal operation
    DAILY_HALT = "DAILY_HALT"            # Daily loss limit breached; resets next day
    KILLED = "KILLED"                    # Total drawdown kill switch triggered; manual reset required
    PAUSED = "PAUSED"                    # Manually paused by operator or OpenClaw agent


@dataclass
class Position:
    """Represents an open or closed position."""
    order_id: str
    token_id: str
    market_question: str
    side: str
    entry_price: float
    size_shares: float
    cost_usdc: float
    opened_at: float
    asset: str = ""          # "btc", "eth", or "sol" — used to release per-asset lock
    direction: str = ""      # "UP" or "DOWN" — which outcome token was bought
    condition_id: str = ""   # market conditionId (0x hex) — needed for on-chain redeem
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_usdc: Optional[float] = None
    paper_mode: bool = True

    @property
    def duration_seconds(self) -> float:
        end = self.closed_at or time.time()
        return end - self.opened_at


@dataclass
class DumpHedgePosition:
    """Represents an open or closed dump-hedge two-leg position."""
    dh_id: str
    yes_order_id: str
    no_order_id: str
    yes_token_id: str
    no_token_id: str
    market_question: str
    asset: str
    yes_entry_price: float
    no_entry_price: float
    combined_entry_price: float    # yes + no at fill time
    size_shares: float             # shares of each leg (same quantity both)
    combined_cost_usdc: float      # combined_entry_price * size_shares
    locked_profit_usdc: float      # (1.0 - combined_entry_price) * size_shares
    opened_at: float
    paper_mode: bool = True
    closed_at: Optional[float] = None
    yes_exit_price: Optional[float] = None
    no_exit_price: Optional[float] = None
    pnl_usdc: Optional[float] = None
    exit_reason: str = ""

    @property
    def duration_seconds(self) -> float:
        end = self.closed_at or time.time()
        return end - self.opened_at


@dataclass
class RiskState:
    """Snapshot of the current risk state for reporting."""
    status: str
    current_balance: float
    starting_balance: float
    peak_balance: float
    daily_starting_balance: float
    total_pnl: float
    total_pnl_pct: float
    daily_pnl: float
    daily_pnl_pct: float
    drawdown_from_peak: float
    drawdown_from_peak_pct: float
    open_positions: int
    open_dh_positions: int
    total_trades: int
    total_dh_trades: int
    winning_trades: int
    win_rate: float
    kill_switch_triggered: bool
    daily_halt_triggered: bool
    circuit_breaker_active: bool
    circuit_breaker_resume_at: float
    la_pnl: float
    dh_pnl: float
    asset_stats: dict  # asset → {trades, wins, pnl, win_rate}


class RiskManager:
    """
    Tracks portfolio state, enforces position limits, and implements
    automatic trading halts when risk thresholds are breached.

    This is the most critical component of the bot. A strategy with a
    55% win rate but proper Kelly sizing will grow capital. A strategy
    with a bug in position sizing that allows an 80% position will blow
    up on the inevitable losing trade.
    """

    def __init__(
        self,
        starting_balance: float,
        max_position_fraction: float = 0.08,
        daily_loss_limit: float = 0.20,
        total_drawdown_kill: float = 0.40,
        max_concurrent_positions: int = 3,
        circuit_breaker_enabled: bool = True,
        circuit_breaker_min_losses: int = 3,
        circuit_breaker_window: int = 5,
        circuit_breaker_loss_pct: float = 0.02,
        circuit_breaker_pause_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            starting_balance: Initial USDC balance.
            max_position_fraction: Max single position as fraction of balance.
            daily_loss_limit: Halt trading if daily loss exceeds this fraction.
            total_drawdown_kill: Kill switch if total drawdown exceeds this fraction.
            max_concurrent_positions: Maximum number of simultaneously open positions.
        """
        self.max_position_fraction = max_position_fraction
        self.daily_loss_limit = daily_loss_limit
        self.total_drawdown_kill = total_drawdown_kill
        self.max_concurrent_positions = max_concurrent_positions
        self.circuit_breaker_enabled = circuit_breaker_enabled
        self.circuit_breaker_min_losses = circuit_breaker_min_losses
        self.circuit_breaker_loss_pct = circuit_breaker_loss_pct
        self.circuit_breaker_pause_seconds = circuit_breaker_pause_seconds

        # Balance tracking
        self._starting_balance: float = starting_balance
        self._current_balance: float = starting_balance
        self._peak_balance: float = starting_balance
        self._daily_starting_balance: float = starting_balance
        self._daily_reset_time: float = self._next_midnight()

        # Status
        self._status: TradingStatus = TradingStatus.ACTIVE
        self._kill_reason: Optional[str] = None

        # Position tracking
        self._open_positions: Dict[str, Position] = {}
        self._closed_positions: List[Position] = []

        # Dump-hedge position tracking
        self._open_dh_positions: Dict[str, DumpHedgePosition] = {}
        self._closed_dh_positions: List[DumpHedgePosition] = []

        # Trade statistics
        self._total_trades: int = 0
        self._winning_trades: int = 0
        self._total_pnl: float = 0.0
        self._total_dh_trades: int = 0
        # Per-strategy PnL attribution
        self._la_pnl: float = 0.0
        self._dh_pnl: float = 0.0

        self._asset_trades: Dict[str, int] = {}
        self._asset_wins: Dict[str, int] = {}
        self._asset_pnl: Dict[str, float] = {}

        # Circuit breaker: per-strategy rolling windows + timed-pause state.
        # Separate deques prevent DH wins from masking LA losses (and vice versa).
        self._recent_la_pnls: Deque[float] = deque(maxlen=circuit_breaker_window)
        self._recent_dh_pnls: Deque[float] = deque(maxlen=circuit_breaker_window)
        self._circuit_breaker_resume_at: float = 0.0

        logger.info(
            "RiskManager initialized | Balance: $%.2f | "
            "Max position: %.0f%% | Daily limit: -%.0f%% | Kill switch: -%.0f%%",
            starting_balance,
            max_position_fraction * 100,
            daily_loss_limit * 100,
            total_drawdown_kill * 100,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def status(self) -> TradingStatus:
        return self._status

    @property
    def is_trading_allowed(self) -> bool:
        """Return True if the bot is allowed to place new trades."""
        self._check_daily_reset()
        self._check_circuit_breaker_resume()
        return self._status == TradingStatus.ACTIVE

    @property
    def current_balance(self) -> float:
        return self._current_balance

    @property
    def open_position_count(self) -> int:
        """Total open positions across both latency-arb and dump-hedge strategies."""
        return len(self._open_positions) + len(self._open_dh_positions)

    def can_open_position(self, position_size_usdc: float) -> tuple[bool, str]:
        """
        Check whether a new latency-arb position can be opened given current risk state.

        Returns:
            (allowed: bool, reason: str)
        """
        if not self.is_trading_allowed:
            return False, f"Trading halted: {self._status.value} — {self._kill_reason or 'N/A'}"

        total_open = len(self._open_positions) + len(self._open_dh_positions)
        if total_open >= self.max_concurrent_positions:
            return False, (
                f"Max concurrent positions reached "
                f"({self.max_concurrent_positions})"
            )

        max_allowed = self._current_balance * self.max_position_fraction
        if position_size_usdc > max_allowed:
            return False, (
                f"Position size ${position_size_usdc:.2f} exceeds max "
                f"${max_allowed:.2f} ({self.max_position_fraction:.0%} of balance)"
            )

        if position_size_usdc > self._current_balance:
            return False, (
                f"Insufficient balance: need ${position_size_usdc:.2f}, "
                f"have ${self._current_balance:.2f}"
            )

        return True, "OK"

    def register_trade_open(self, position: Position) -> None:
        """Record a newly opened position and deduct cost from balance."""
        self._open_positions[position.order_id] = position
        self._current_balance -= position.cost_usdc

        logger.info(
            "Position OPENED | %s | $%.2f USDC | Balance: $%.2f",
            position.order_id,
            position.cost_usdc,
            self._current_balance,
        )

    def register_trade_close(
        self,
        order_id: str,
        exit_price: float,
        exit_timestamp: Optional[float] = None,
        actual_proceeds_usdc: Optional[float] = None,
    ) -> Optional[Position]:
        position = self._open_positions.pop(order_id, None)
        if position is None:
            logger.warning("register_trade_close: order_id %s not found", order_id)
            return None

        if not (0 <= exit_price <= 1.0):
            logger.error(
                "Invalid exit_price %.3f for %s | Expected: 0.000-1.000 | "
                "Entry: %.3f | Market: %s",
                exit_price, order_id, position.entry_price, position.market_question
            )
            self._open_positions[order_id] = position
            return None

        position.closed_at = exit_timestamp or time.time()
        position.exit_price = exit_price

        # PnL based on actual cash flows when available (actual_proceeds_usdc from
        # the real sell fill). Falls back to formula for paper mode where no real
        # fill data exists.
        if actual_proceeds_usdc is not None:
            pnl = actual_proceeds_usdc - position.cost_usdc
            self._current_balance += actual_proceeds_usdc
        else:
            pnl = (exit_price - position.entry_price) * position.size_shares
            self._current_balance += position.cost_usdc + pnl
        position.pnl_usdc = pnl
        self._total_pnl += pnl
        self._la_pnl += pnl

        self._total_trades += 1
        won = pnl > 0

        if won:
            self._winning_trades += 1

        self._record_asset_close(position.asset, pnl, won)

        # Update peak balance
        if self._current_balance > self._peak_balance:
            self._peak_balance = self._current_balance

        self._closed_positions.append(position)
        # Cap memory: keep only the most recent 1000 closed LA positions
        if len(self._closed_positions) > 1000:
            self._closed_positions = self._closed_positions[-1000:]

        logger.info(
            "Position CLOSED | %s | PnL: $%+.2f | Balance: $%.2f | "
            "Win rate: %.1f%%",
            order_id,
            pnl,
            self._current_balance,
            self.win_rate * 100,
        )

        # Check risk thresholds after every close
        self._check_risk_thresholds()
        self._recent_la_pnls.append(pnl)
        self._check_circuit_breaker()

        return position

    # ─────────────────────────────────────────────────────────────────────────
    # Dump-Hedge Position Methods
    # ─────────────────────────────────────────────────────────────────────────

    def can_open_dh_position(self, combined_cost_usdc: float) -> tuple[bool, str]:
        """
        Check whether a new dump-hedge position can be opened.

        DH positions count against the shared max_concurrent_positions limit.
        Cost check uses combined_cost_usdc (both legs together).

        Returns:
            (allowed: bool, reason: str)
        """
        if not self.is_trading_allowed:
            return False, f"Trading halted: {self._status.value} — {self._kill_reason or 'N/A'}"

        total_open = len(self._open_positions) + len(self._open_dh_positions)
        if total_open >= self.max_concurrent_positions:
            return False, (
                f"Max concurrent positions reached ({self.max_concurrent_positions})"
            )

        max_allowed = self._current_balance * self.max_position_fraction
        if combined_cost_usdc > max_allowed:
            return False, (
                f"DH cost ${combined_cost_usdc:.2f} exceeds max "
                f"${max_allowed:.2f} ({self.max_position_fraction:.0%} of balance)"
            )

        if combined_cost_usdc > self._current_balance:
            return False, (
                f"Insufficient balance: need ${combined_cost_usdc:.2f}, "
                f"have ${self._current_balance:.2f}"
            )

        return True, "OK"

    def register_dh_open(self, position: DumpHedgePosition) -> None:
        """Record a newly opened dump-hedge position and deduct cost from balance."""
        self._open_dh_positions[position.dh_id] = position
        self._current_balance -= position.combined_cost_usdc

        logger.info(
            "DH Position OPENED | %s | $%.2f USDC | Locked: $%.2f | Balance: $%.2f",
            position.dh_id,
            position.combined_cost_usdc,
            position.locked_profit_usdc,
            self._current_balance,
        )

    def register_dh_close(
        self,
        dh_id: str,
        yes_exit_price: float,
        no_exit_price: float,
        exit_reason: str = "",
        exit_timestamp: Optional[float] = None,
        actual_proceeds_usdc: Optional[float] = None,
    ) -> Optional[DumpHedgePosition]:
        """
        Close a dump-hedge position and update balance.

        For paper mode or when actual_proceeds_usdc is not available, PnL is
        computed as (combined_exit - combined_entry) * size_shares.
        """
        position = self._open_dh_positions.pop(dh_id, None)
        if position is None:
            logger.warning("register_dh_close: dh_id %s not found", dh_id)
            return None

        position.closed_at = exit_timestamp or time.time()
        position.yes_exit_price = yes_exit_price
        position.no_exit_price = no_exit_price
        position.exit_reason = exit_reason

        if actual_proceeds_usdc is not None:
            pnl = actual_proceeds_usdc - position.combined_cost_usdc
            self._current_balance += actual_proceeds_usdc
        else:
            combined_exit = yes_exit_price + no_exit_price
            pnl = (combined_exit - position.combined_entry_price) * position.size_shares
            self._current_balance += position.combined_cost_usdc + pnl
        position.pnl_usdc = pnl
        self._total_pnl += pnl
        self._dh_pnl += pnl

        self._total_dh_trades += 1
        won = pnl > 0

        if won:
            self._winning_trades += 1

        self._record_asset_close(position.asset, pnl, won)

        if self._current_balance > self._peak_balance:
            self._peak_balance = self._current_balance

        self._closed_dh_positions.append(position)
        # Cap memory: keep only the most recent 1000 closed DH positions
        if len(self._closed_dh_positions) > 1000:
            self._closed_dh_positions = self._closed_dh_positions[-1000:]

        logger.info(
            "DH Position CLOSED | %s | PnL: $%+.2f | Reason: %s | Balance: $%.2f",
            dh_id,
            pnl,
            exit_reason,
            self._current_balance,
        )

        self._check_risk_thresholds()
        self._recent_dh_pnls.append(pnl)
        self._check_circuit_breaker()
        return position

    def update_balance(self, new_balance: float) -> None:
        """
        Update the current balance from an external source (e.g., on-chain query).
        Also checks risk thresholds.
        """
        old = self._current_balance
        self._current_balance = new_balance
        if new_balance > self._peak_balance:
            self._peak_balance = new_balance
        logger.debug(
            "Balance updated: $%.2f → $%.2f (Δ$%+.2f)",
            old,
            new_balance,
            new_balance - old,
        )
        self._check_risk_thresholds()

    def set_daily_starting_balance(self, balance: float) -> None:
        """
        Set the daily starting balance baseline used for daily-loss-limit checks.
        Call once at bot startup after syncing live balance from the chain.
        Must be called BEFORE update_balance() so _check_risk_thresholds sees 0% daily loss.
        """
        self._daily_starting_balance = balance
        logger.debug("Daily starting balance set to $%.2f", balance)

    def set_live_starting_balance(self, balance: float) -> None:
        """
        Completely reset all baseline balance metrics to a new live value.
        Used at startup to replace the initial paper configuration with 
        the actual on-chain wallet balance so that peak/drawdown calculations
        start from a true zero-point.
        """
        self._starting_balance = balance
        self._current_balance = balance
        self._peak_balance = balance
        self._daily_starting_balance = balance
        
        if self._status in (TradingStatus.DAILY_HALT, TradingStatus.KILLED):
            self._status = TradingStatus.ACTIVE
            self._kill_reason = None
            
        logger.info("Live baseline balances reset to $%.2f", balance)

    def pause(self, reason: str = "Manual pause") -> None:
        """Pause trading (can be resumed)."""
        if self._status == TradingStatus.ACTIVE:
            self._status = TradingStatus.PAUSED
            self._kill_reason = reason
            logger.warning("Trading PAUSED: %s", reason)

    def resume(self) -> bool:
        """Resume trading from a PAUSED state. Cannot resume from KILLED."""
        if self._status == TradingStatus.KILLED:
            logger.error(
                "Cannot resume: kill switch has been triggered. "
                "Manual investigation required before restarting."
            )
            return False
        if self._status == TradingStatus.PAUSED:
            self._status = TradingStatus.ACTIVE
            self._kill_reason = None
            logger.info("Trading RESUMED.")
            return True
        return True

    def reset_kill_switch(self, confirm: bool = False) -> bool:
        """
        Manually reset the kill switch after investigation.
        Requires explicit confirmation to prevent accidental resets.
        """
        if not confirm:
            logger.error(
                "reset_kill_switch requires confirm=True. "
                "Ensure you have investigated the cause before resetting."
            )
            return False
        if self._status == TradingStatus.KILLED:
            self._status = TradingStatus.ACTIVE
            self._kill_reason = None
            self._daily_starting_balance = self._current_balance
            logger.warning(
                "KILL SWITCH RESET manually. Trading resumed. "
                "Current balance: $%.2f",
                self._current_balance,
            )
            return True
        return True

    @property
    def win_rate(self) -> float:
        """Return the historical win rate across all closed trades (LA + DH)."""
        closed = len(self._closed_positions) + len(self._closed_dh_positions)
        if closed == 0:
            return 0.0
        return self._winning_trades / closed

    def get_state(self) -> RiskState:
        """Return a complete snapshot of the current risk state."""
        total_pnl_pct = (
            (self._current_balance - self._starting_balance) / self._starting_balance
            if self._starting_balance > 0
            else 0.0
        )
        daily_pnl = self._current_balance - self._daily_starting_balance
        daily_pnl_pct = (
            daily_pnl / self._daily_starting_balance
            if self._daily_starting_balance > 0
            else 0.0
        )
        drawdown = self._peak_balance - self._current_balance
        drawdown_pct = drawdown / self._peak_balance if self._peak_balance > 0 else 0.0

        return RiskState(
            status=self._status.value,
            current_balance=self._current_balance,
            starting_balance=self._starting_balance,
            peak_balance=self._peak_balance,
            daily_starting_balance=self._daily_starting_balance,
            total_pnl=self._total_pnl,
            total_pnl_pct=total_pnl_pct,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            drawdown_from_peak=drawdown,
            drawdown_from_peak_pct=drawdown_pct,
            open_positions=len(self._open_positions),
            open_dh_positions=len(self._open_dh_positions),
            total_trades=self._total_trades,
            total_dh_trades=self._total_dh_trades,
            winning_trades=self._winning_trades,
            win_rate=self.win_rate,
            kill_switch_triggered=(self._status == TradingStatus.KILLED),
            daily_halt_triggered=(self._status == TradingStatus.DAILY_HALT),
            circuit_breaker_active=(
                self._status == TradingStatus.PAUSED
                and self._circuit_breaker_resume_at > 0
            ),
            circuit_breaker_resume_at=self._circuit_breaker_resume_at,
            la_pnl=self._la_pnl,
            dh_pnl=self._dh_pnl,
            asset_stats=self._build_asset_stats(),
        )

    def _build_asset_stats(self) -> dict:
        """Return per-asset {trades, wins, pnl, win_rate} dict (sorted by PnL desc)."""
        result = {}
        for asset in self._asset_trades:
            t = self._asset_trades[asset]
            w = self._asset_wins.get(asset, 0)
            p = self._asset_pnl.get(asset, 0.0)
            result[asset] = {
                "trades": t,
                "wins": w,
                "pnl": p,
                "win_rate": w / t if t > 0 else 0.0,
            }
        return dict(sorted(result.items(), key=lambda kv: kv[1]["pnl"], reverse=True))

    def _record_asset_close(self, asset: str, pnl: float, won: bool) -> None:
        """Update per-asset counters on every trade close."""
        if not asset:
            return
        self._asset_trades[asset] = self._asset_trades.get(asset, 0) + 1
        if won:
            self._asset_wins[asset] = self._asset_wins.get(asset, 0) + 1
        self._asset_pnl[asset] = self._asset_pnl.get(asset, 0.0) + pnl

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Methods
    # ─────────────────────────────────────────────────────────────────────────

    def _check_risk_thresholds(self) -> None:
        """Evaluate all risk thresholds and trigger halts if necessary."""
        if self._status == TradingStatus.KILLED:
            return  # Already killed; no further checks needed

        # Total drawdown kill switch
        if self._peak_balance > 0:
            drawdown_pct = (self._peak_balance - self._current_balance) / self._peak_balance
            if drawdown_pct >= self.total_drawdown_kill:
                self._trigger_kill_switch(
                    f"Total drawdown {drawdown_pct:.1%} exceeded kill threshold "
                    f"{self.total_drawdown_kill:.1%}. "
                    f"Peak: ${self._peak_balance:.2f} → Current: ${self._current_balance:.2f}"
                )
                return

        # Daily loss limit
        if self._daily_starting_balance > 0:
            daily_loss_pct = (
                self._daily_starting_balance - self._current_balance
            ) / self._daily_starting_balance
            if daily_loss_pct >= self.daily_loss_limit:
                self._trigger_daily_halt(
                    f"Daily loss {daily_loss_pct:.1%} exceeded limit "
                    f"{self.daily_loss_limit:.1%}. "
                    f"Daily start: ${self._daily_starting_balance:.2f} → "
                    f"Current: ${self._current_balance:.2f}"
                )

    def _check_circuit_breaker(self) -> None:
        """
        Trigger a timed trading pause if recent losses in EITHER strategy exceed
        the circuit breaker threshold.

        Checks LA and DH independently so a streak of DH wins cannot hide a
        consecutive LA loss run (and vice versa).

        Fires when ALL of the following are true for a given strategy window:
          - circuit_breaker_enabled is True
          - At least circuit_breaker_min_losses of the last N trades are losses
          - The cumulative loss > circuit_breaker_loss_pct * current_balance
          - Bot is currently ACTIVE (don't stack pauses)
        """
        if not self.circuit_breaker_enabled:
            return
        if self._status != TradingStatus.ACTIVE:
            return

        threshold = self._current_balance * self.circuit_breaker_loss_pct

        for strategy, pnls in (("LA", self._recent_la_pnls), ("DH", self._recent_dh_pnls)):
            if len(pnls) < self.circuit_breaker_min_losses:
                continue

            losses = [p for p in pnls if p < 0]
            if len(losses) < self.circuit_breaker_min_losses:
                continue

            cumulative_loss = abs(sum(losses))
            if cumulative_loss < threshold:
                continue

            resume_at = time.time() + self.circuit_breaker_pause_seconds
            self._circuit_breaker_resume_at = resume_at
            self._status = TradingStatus.PAUSED
            self._kill_reason = (
                f"Circuit breaker ({strategy}): {len(losses)} losses in last "
                f"{len(pnls)} {strategy} trades, cumulative loss "
                f"${cumulative_loss:.2f} > {self.circuit_breaker_loss_pct:.0%} "
                f"of balance. Pausing {self.circuit_breaker_pause_seconds:.0f}s."
            )
            logger.warning(
                "CIRCUIT BREAKER triggered [%s] — trading paused for %.0fs. "
                "%d/%d recent %s trades are losses, cumulative $%.2f. Resumes at %s UTC.",
                strategy,
                self.circuit_breaker_pause_seconds,
                len(losses),
                len(pnls),
                strategy,
                cumulative_loss,
                datetime.datetime.utcfromtimestamp(resume_at).strftime("%H:%M:%S"),
            )
            break  # One trigger per call is enough

    def _check_circuit_breaker_resume(self) -> None:
        """Auto-resume after circuit breaker pause expires."""
        if (
            self._status == TradingStatus.PAUSED
            and self._circuit_breaker_resume_at > 0
            and time.time() >= self._circuit_breaker_resume_at
        ):
            self._circuit_breaker_resume_at = 0.0
            self._status = TradingStatus.ACTIVE
            self._kill_reason = None
            self._recent_la_pnls.clear()
            self._recent_dh_pnls.clear()
            logger.info("Circuit breaker pause expired — trading RESUMED.")

    def _trigger_kill_switch(self, reason: str) -> None:
        """Trigger the permanent kill switch."""
        self._status = TradingStatus.KILLED
        self._kill_reason = reason
        # Each line is a separate log entry so every line has a timestamp
        # and the dashboard _LOG_RE parser can display them cleanly.
        logger.critical("KILL SWITCH TRIGGERED — ALL TRADING HALTED")
        logger.critical("Reason: %s", reason)
        logger.critical("Call reset_kill_switch(confirm=True) to resume.")

    def _trigger_daily_halt(self, reason: str) -> None:
        """Trigger a daily trading halt (resets at midnight)."""
        if self._status != TradingStatus.DAILY_HALT:
            self._status = TradingStatus.DAILY_HALT
            self._kill_reason = reason
            logger.warning(
                "DAILY HALT TRIGGERED — Trading paused until midnight UTC.\n"
                "Reason: %s",
                reason,
            )

    def _check_daily_reset(self) -> None:
        """Reset the daily halt at midnight UTC."""
        if time.time() >= self._daily_reset_time:
            if self._status == TradingStatus.DAILY_HALT:
                self._status = TradingStatus.ACTIVE
                self._kill_reason = None
                logger.info(
                    "Daily halt reset at midnight UTC. Trading resumed. "
                    "New daily starting balance: $%.2f",
                    self._current_balance,
                )
            self._daily_starting_balance = self._current_balance
            self._daily_reset_time = self._next_midnight()

    @staticmethod
    def _next_midnight() -> float:
        """Return the Unix timestamp of the next midnight UTC."""
        now = datetime.datetime.now(datetime.timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow += datetime.timedelta(days=1)
        return tomorrow.timestamp()
