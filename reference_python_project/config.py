"""
╔══════════════════════════════════════════════════════════════════════════════╗
║          POLYMARKET LATENCY ARBITRAGE BOT — OPENCLAW EDITION               ║
║          Configuration & Environment Variable Loader                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────────────────────────────────────
# Polymarket CLOB Settings
# ─────────────────────────────────────────────────────────────────────────────
POLYMARKET_HOST: str = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_CHAIN_ID: int = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))  # Polygon mainnet
POLYMARKET_PRIVATE_KEY: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER: str = os.getenv("POLYMARKET_FUNDER", "")
POLYMARKET_SIGNATURE_TYPE: int = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))

# ─────────────────────────────────────────────────────────────────────────────
# Trading Markets
# ─────────────────────────────────────────────────────────────────────────────
_VALID_MARKETS = {"btc", "eth", "sol", "xrp"}
_raw_markets = os.getenv("MARKETS", "btc,eth,sol")
TRADING_MARKETS: List[str] = [
    m.strip().lower() for m in _raw_markets.split(",")
    if m.strip().lower() in _VALID_MARKETS
] or ["btc"]  # fallback to btc if all values are invalid

# ─────────────────────────────────────────────────────────────────────────────
# Binance WebSocket Settings
# ─────────────────────────────────────────────────────────────────────────────
BINANCE_RECONNECT_DELAY: float = float(os.getenv("BINANCE_RECONNECT_DELAY", "2.0"))

# ─────────────────────────────────────────────────────────────────────────────
# Strategy Mode
# ─────────────────────────────────────────────────────────────────────────────
_raw_strategy = os.getenv("STRATEGY", "latency_arb").lower()
if _raw_strategy not in ("latency_arb", "dump_hedge", "both"):
    raise ValueError(
        f"STRATEGY must be latency_arb, dump_hedge, or both. Got: {_raw_strategy}"
    )
STRATEGY: str = _raw_strategy

# ─────────────────────────────────────────────────────────────────────────────
# Trade Window
# ─────────────────────────────────────────────────────────────────────────────
# Which Polymarket up/down market series to trade: 5 or 15 (minutes).
# 5  → "Bitcoin Up or Down - 5 Minutes"  (resolves every 5 min, higher volume)
# 15 → "Bitcoin Up or Down - 15 Minutes" (resolves every 15 min, lower noise)
_raw_window = int(os.getenv("TRADE_WINDOW_MINUTES", "5"))
if _raw_window not in (5, 15):
    raise ValueError(
        f"TRADE_WINDOW_MINUTES must be 5 or 15, got {_raw_window}. "
        "Check your .env file."
    )
TRADE_WINDOW_MINUTES: int = _raw_window

# ─────────────────────────────────────────────────────────────────────────────
# Edge Detection Parameters
# ─────────────────────────────────────────────────────────────────────────────
# Minimum BTC price move (in USD) to trigger edge evaluation
EDGE_MIN_PRICE_MOVE: float = float(os.getenv("EDGE_MIN_PRICE_MOVE", "50.0"))
# Minimum implied edge (probability advantage) to place a trade
EDGE_MIN_EDGE_THRESHOLD: float = float(os.getenv("EDGE_MIN_EDGE_THRESHOLD", "0.04"))
# Polymarket lag window in seconds (the ~2.7-second opportunity window)
EDGE_LAG_WINDOW_SECONDS: float = float(os.getenv("EDGE_LAG_WINDOW_SECONDS", "2.7"))
# Cooldown period after a trade to avoid double-entry
EDGE_COOLDOWN_SECONDS: float = float(os.getenv("EDGE_COOLDOWN_SECONDS", "5.0"))
# Minimum seconds remaining in the market window before entering a trade.
# Must be enough time to exit before resolution. Auto-derived if not set:
#   5-min window  → 60s  (last 20% of window blocked)
#   15-min window → 180s (last 20% of window blocked)
_edge_min_secs_raw = os.getenv("EDGE_MIN_SECONDS_REMAINING")
EDGE_MIN_SECONDS_REMAINING: float = (
    float(_edge_min_secs_raw) if _edge_min_secs_raw
    else float(TRADE_WINDOW_MINUTES * 60 * 0.20)  # 20% of window
)
# Minimum market liquidity (USDC) required to trade
# Note: BTC 5-minute up/down markets typically have $5k-$50k liquidity per window.
# The old default of 50000 was too high and filtered out all 5m markets.
EDGE_MIN_MARKET_LIQUIDITY: float = float(os.getenv("EDGE_MIN_MARKET_LIQUIDITY", "1000.0"))

# Entry price zone: only trade tokens priced within this window around 50¢.
# Tokens far from 0.5 mean the market has already made its determination —
# our 2.7s lag edge cannot overcome accumulated directional evidence.
# Example: a token at 12¢ means 12+ minutes of BTC moving DOWN. Don't fight it.
EDGE_MIN_ENTRY_PRICE: float = float(os.getenv("EDGE_MIN_ENTRY_PRICE", "0.38"))
EDGE_MAX_ENTRY_PRICE: float = float(os.getenv("EDGE_MAX_ENTRY_PRICE", "0.62"))

# Minimum fair value conviction from the Binance model before comparing to market price.
# The sigmoid model must output P(direction) >= 0.5 + this value to fire a signal.
# Prevents trades where price_now ≈ PTB (sigmoid ≈ 0.50) creating fake edge against
# cheap tokens — the only real edge comes when the model has genuine directional conviction.
# 0.05 → model must say ≥55% to fire (recommended)
# 0.08 → model must say ≥58% (more selective, fewer signals)
EDGE_MIN_FAIR_VALUE_STRENGTH: float = float(os.getenv("EDGE_MIN_FAIR_VALUE_STRENGTH", "0.05"))

# ─────────────────────────────────────────────────────────────────────────────
# Dump-Hedge Strategy Parameters
# ─────────────────────────────────────────────────────────────────────────────
# Maximum combined YES + NO price to enter. Below $1.00 = structural profit.
DH_SUM_TARGET: float = float(os.getenv("DH_SUM_TARGET", "0.95"))
# Minimum discount (1.0 - combined) per share to be worth the transaction cost.
DH_MIN_DISCOUNT: float = float(os.getenv("DH_MIN_DISCOUNT", "0.03"))
# Total USDC to spend per DH trade (split across both legs).
DH_FIXED_BET_USDC: float = float(os.getenv("DH_FIXED_BET_USDC", "50.0"))
# Exit when combined SELL price >= combined BUY price + this fraction of locked profit.
DH_EARLY_EXIT_PROFIT_FRACTION: float = float(os.getenv("DH_EARLY_EXIT_PROFIT_FRACTION", "0.70"))
# Force-close DH position after this many seconds (default: same as position timeout).
_dh_timeout_env = os.getenv("DH_TIMEOUT_SECONDS")
DH_TIMEOUT_SECONDS: float = (
    float(_dh_timeout_env) if _dh_timeout_env
    else float(TRADE_WINDOW_MINUTES * 60 * 0.90)   # close at 90% of window
)
# Cooldown between DH signals on the same asset.
DH_COOLDOWN_SECONDS: float = float(os.getenv("DH_COOLDOWN_SECONDS", "30.0"))

# ─────────────────────────────────────────────────────────────────────────────
# Risk Management Parameters
# ─────────────────────────────────────────────────────────────────────────────
# Maximum single position size as fraction of total portfolio
RISK_MAX_POSITION_FRACTION: float = float(os.getenv("RISK_MAX_POSITION_FRACTION", "0.08"))
# Daily loss limit as fraction of starting daily balance (halt trading if breached)
RISK_DAILY_LOSS_LIMIT: float = float(os.getenv("RISK_DAILY_LOSS_LIMIT", "0.20"))
# Total drawdown kill switch (stop all trading permanently until manual reset)
RISK_TOTAL_DRAWDOWN_KILL: float = float(os.getenv("RISK_TOTAL_DRAWDOWN_KILL", "0.40"))
# Kelly fraction multiplier (fractional Kelly for safety, e.g. 0.5 = half-Kelly)
RISK_KELLY_FRACTION: float = float(os.getenv("RISK_KELLY_FRACTION", "0.5"))
# Maximum number of concurrent open positions
RISK_MAX_CONCURRENT_POSITIONS: int = int(os.getenv("RISK_MAX_CONCURRENT_POSITIONS", "3"))

# Fixed bet amount in USDC per trade.
# Set to a positive value (e.g. 50.0) to use a fixed bet instead of Kelly sizing.
# Set to 0.0 (default) to let Kelly Criterion calculate the optimal size automatically.
RISK_FIXED_BET_USDC: float = float(os.getenv("RISK_FIXED_BET_USDC", "0.0"))

# Kelly Criterion toggle.
# KELLY_ENABLED=true  → use Kelly formula to size each bet (ignores RISK_FIXED_BET_USDC)
# KELLY_ENABLED=false → use RISK_FIXED_BET_USDC as a fixed bet amount
KELLY_ENABLED: bool = os.getenv("KELLY_ENABLED", "false").lower() == "true"
# KELLY_ADAPTIVE_ENABLED=true → scale Kelly fraction up/down based on recent win rate.
#   Reduces bet size when win_rate < 45% (model underperforming), scales up slightly
#   when win_rate > 55% (model outperforming). Floor is RISK_KELLY_FRACTION * 0.1.
KELLY_ADAPTIVE_ENABLED: bool = os.getenv("KELLY_ADAPTIVE_ENABLED", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# Position Exit Thresholds (Stop Loss / Take Profit)
# ─────────────────────────────────────────────────────────────────────────────
# Take profit: exit when YES token price reaches this level (0.72 = 72 cents)
# A YES token bought at ~0.50 and sold at 0.72 returns +44% gross on the token
TAKE_PROFIT_PRICE: float = float(os.getenv("TAKE_PROFIT_PRICE", "0.72"))

# Take profit: also exit when PnL percentage reaches this target regardless of price
# 0.12 = +12% unrealised gain on position cost
TAKE_PROFIT_PNL: float = float(os.getenv("TAKE_PROFIT_PNL", "0.12"))

# Stop loss: exit when PnL percentage falls below this threshold (negative value)
# -0.20 = -20% loss on position cost.  Set to 0.0 to disable stop-loss.
STOP_LOSS_PNL: float = float(os.getenv("STOP_LOSS_PNL", "-0.20"))

# Near-resolution take: exit when price >= this value (market about to resolve YES)
# 0.92 = 92 cents — essentially guaranteed win, bank it early
NEAR_WIN_PRICE: float = float(os.getenv("NEAR_WIN_PRICE", "0.92"))

# Near-resolution cut: exit when price <= this value (market about to resolve NO)
# 0.08 = 8 cents — essentially guaranteed loss, cut early to recycle capital
NEAR_LOSS_PRICE: float = float(os.getenv("NEAR_LOSS_PRICE", "0.08"))

# Position timeout: close any position older than this many seconds.
# Defaults to TRADE_WINDOW_MINUTES × 60 so positions always close before the
# market window resolves.  Override explicitly if needed.
_pos_timeout_env = os.getenv("POSITION_TIMEOUT_SECONDS")
POSITION_TIMEOUT_SECONDS: float = (
    float(_pos_timeout_env) if _pos_timeout_env
    else float(TRADE_WINDOW_MINUTES * 60)
)

# ─────────────────────────────────────────────────────────────────────────────
# Paper Trading Mode
# ─────────────────────────────────────────────────────────────────────────────
# Paper mode is strictly driven by CLI arguments (--paper / --live). Default is True.
PAPER_MODE: bool = True
PAPER_STARTING_BALANCE: float = float(os.getenv("PAPER_STARTING_BALANCE", "1000.0"))
# Inject random fill slippage in paper mode to simulate real market conditions.
# 0.005 = ±0.5% noise on fill price (adverse direction). 0.0 = disabled.
PAPER_SLIPPAGE_PCT: float = float(os.getenv("PAPER_SLIPPAGE_PCT", "0.005"))

# ─────────────────────────────────────────────────────────────────────────────
# OpenClaw Agent Integration
# ─────────────────────────────────────────────────────────────────────────────
OPENCLAW_ENABLED: bool = os.getenv("OPENCLAW_ENABLED", "false").lower() == "true"
OPENCLAW_API_KEY: str = os.getenv("OPENCLAW_API_KEY", "")
OPENCLAW_API_URL: str = os.getenv("OPENCLAW_API_URL", "https://app.openclaw.ai/api")
OPENCLAW_AGENT_ID: str = os.getenv("OPENCLAW_AGENT_ID", "")
# How often to push a performance summary to the OpenClaw agent (seconds)
OPENCLAW_REPORT_INTERVAL: int = int(os.getenv("OPENCLAW_REPORT_INTERVAL", "300"))

# ─────────────────────────────────────────────────────────────────────────────
# Proxy
# ─────────────────────────────────────────────────────────────────────────────
# Optional proxy URL for all outbound connections (REST, WebSocket, Telegram).
# Supports SOCKS5 (socks5://user:pass@host:port), SOCKS4, or HTTP proxies.
# Leave empty to connect directly.
PROXY_URL: str = os.getenv("PROXY_URL", "")

# ─────────────────────────────────────────────────────────────────────────────
# Telegram Notifications
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: str = os.getenv("LOG_FILE", "polymarket_bot.log")
DASHBOARD_LOG_LINES: int = int(os.getenv("DASHBOARD_LOG_LINES", "20"))
# Directory where daily trade CSV files are written. Set to "" to disable export.
TRADES_CSV_DIR: str = os.getenv("TRADES_CSV_DIR", "trades")


@dataclass
class BotConfig:
    """Centralised, validated configuration object for the bot."""

    # Polymarket
    polymarket_host: str = POLYMARKET_HOST
    polymarket_chain_id: int = POLYMARKET_CHAIN_ID
    polymarket_private_key: str = POLYMARKET_PRIVATE_KEY
    polymarket_funder: str = POLYMARKET_FUNDER
    polymarket_signature_type: int = POLYMARKET_SIGNATURE_TYPE

    # Markets to trade
    trading_markets: List[str] = field(default_factory=lambda: TRADING_MARKETS)

    # Trade window (5 or 15 minutes)
    trade_window_minutes: int = TRADE_WINDOW_MINUTES

    # Binance
    binance_reconnect_delay: float = BINANCE_RECONNECT_DELAY

    # Edge detection
    edge_min_price_move: float = EDGE_MIN_PRICE_MOVE
    edge_min_edge_threshold: float = EDGE_MIN_EDGE_THRESHOLD
    edge_lag_window_seconds: float = EDGE_LAG_WINDOW_SECONDS
    edge_cooldown_seconds: float = EDGE_COOLDOWN_SECONDS
    edge_min_market_liquidity: float = EDGE_MIN_MARKET_LIQUIDITY
    edge_min_seconds_remaining: float = EDGE_MIN_SECONDS_REMAINING
    edge_min_entry_price: float = EDGE_MIN_ENTRY_PRICE
    edge_max_entry_price: float = EDGE_MAX_ENTRY_PRICE
    edge_min_fair_value_strength: float = EDGE_MIN_FAIR_VALUE_STRENGTH

    # Strategy
    strategy: str = STRATEGY

    # Dump-Hedge
    dh_sum_target: float = DH_SUM_TARGET
    dh_min_discount: float = DH_MIN_DISCOUNT
    dh_fixed_bet_usdc: float = DH_FIXED_BET_USDC
    dh_early_exit_profit_fraction: float = DH_EARLY_EXIT_PROFIT_FRACTION
    dh_timeout_seconds: float = DH_TIMEOUT_SECONDS
    dh_cooldown_seconds: float = DH_COOLDOWN_SECONDS

    # Risk management
    risk_max_position_fraction: float = RISK_MAX_POSITION_FRACTION
    risk_daily_loss_limit: float = RISK_DAILY_LOSS_LIMIT
    risk_total_drawdown_kill: float = RISK_TOTAL_DRAWDOWN_KILL
    risk_kelly_fraction: float = RISK_KELLY_FRACTION
    risk_max_concurrent_positions: int = RISK_MAX_CONCURRENT_POSITIONS
    # Fixed bet amount in USDC. 0.0 = use Kelly Criterion (default).
    risk_fixed_bet_usdc: float = RISK_FIXED_BET_USDC
    # Kelly toggle: true = Kelly formula, false = fixed bet
    kelly_enabled: bool = KELLY_ENABLED
    # Adaptive Kelly: scale fraction based on recent win rate
    kelly_adaptive_enabled: bool = KELLY_ADAPTIVE_ENABLED

    # Exit thresholds
    take_profit_price: float = TAKE_PROFIT_PRICE
    take_profit_pnl: float = TAKE_PROFIT_PNL
    stop_loss_pnl: float = STOP_LOSS_PNL
    near_win_price: float = NEAR_WIN_PRICE
    near_loss_price: float = NEAR_LOSS_PRICE
    position_timeout_seconds: float = POSITION_TIMEOUT_SECONDS

    # Paper mode
    paper_mode: bool = PAPER_MODE
    paper_starting_balance: float = PAPER_STARTING_BALANCE
    paper_slippage_pct: float = PAPER_SLIPPAGE_PCT

    # OpenClaw
    openclaw_enabled: bool = OPENCLAW_ENABLED
    openclaw_api_key: str = OPENCLAW_API_KEY
    openclaw_api_url: str = OPENCLAW_API_URL
    openclaw_agent_id: str = OPENCLAW_AGENT_ID
    openclaw_report_interval: int = OPENCLAW_REPORT_INTERVAL

    # Proxy (optional — applies to REST, WebSocket, and Telegram)
    proxy_url: str = PROXY_URL

    # Telegram
    telegram_bot_token: str = TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = TELEGRAM_CHAT_ID
    telegram_enabled: bool = TELEGRAM_ENABLED

    # Logging
    log_level: str = LOG_LEVEL
    log_file: str = LOG_FILE
    dashboard_log_lines: int = DASHBOARD_LOG_LINES
    trades_csv_dir: str = TRADES_CSV_DIR

    def validate(self) -> None:
        """Validate critical configuration values before starting the bot."""
        if not self.paper_mode:
            if not self.polymarket_private_key:
                raise ValueError(
                    "POLYMARKET_PRIVATE_KEY must be set when PAPER_MODE=false. "
                    "Never hardcode this value — use a .env file."
                )
            if not self.polymarket_funder:
                raise ValueError(
                    "POLYMARKET_FUNDER address must be set when PAPER_MODE=false."
                )
        if not (0.0 < self.risk_max_position_fraction <= 0.50):
            raise ValueError(
                f"RISK_MAX_POSITION_FRACTION must be between 0 and 0.50 (got {self.risk_max_position_fraction}). "
                "Values above 50% are dangerously high."
            )
        if not (0.0 < self.risk_kelly_fraction <= 1.0):
            raise ValueError("RISK_KELLY_FRACTION must be between 0 and 1.0.")
        if self.risk_fixed_bet_usdc < 0:
            raise ValueError(
                "RISK_FIXED_BET_USDC cannot be negative. "
                "Set to 0.0 to use Kelly sizing, or a positive value for a fixed bet."
            )
        if self.risk_fixed_bet_usdc > 0 and self.paper_mode:
            # In live mode the actual balance is fetched from the blockchain at startup,
            # so we cannot validate the cap here. Only validate in paper mode.
            max_safe = self.paper_starting_balance * self.risk_max_position_fraction
            if self.risk_fixed_bet_usdc > max_safe:
                raise ValueError(
                    f"RISK_FIXED_BET_USDC (${self.risk_fixed_bet_usdc:.2f}) exceeds "
                    f"RISK_MAX_POSITION_FRACTION cap (${max_safe:.2f}). "
                    f"Lower the fixed bet or raise RISK_MAX_POSITION_FRACTION."
                )
        # Dump-hedge minimum bet: each leg needs at least $1.00, so combined >= $2.00.
        if self.strategy in ("dump_hedge", "both") and self.dh_fixed_bet_usdc < 2.0:
            raise ValueError(
                f"DH_FIXED_BET_USDC (${self.dh_fixed_bet_usdc:.2f}) is too small. "
                "Polymarket requires a minimum of $1.00 per leg, so DH_FIXED_BET_USDC "
                "must be at least $2.00. Recommended: $10.00 or higher."
            )
