"""
OPENCLAW · POLYMARKET ARB BOT

Usage:
    python main.py [--paper | --live]

Strategies:
    latency_arb  — exploit ~2.7s Binance→Polymarket lag
    dump_hedge   — buy YES+NO when combined < $1.00 (structural arb)
    both         — run both simultaneously

Environment:
    Copy .env.example to .env and fill in your credentials before running.
    Always run in paper mode for at least 20 trades before going live.
"""


import argparse
import asyncio
import csv
import datetime
import os
import signal
import sys
import time
import uuid
from typing import Optional

# Force UTF-8 on Windows terminals (Git Bash / CMD use CP1252 by default,
# which cannot encode box-drawing / arrow characters used in log messages).
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass  # Non-reconfigurable stream (e.g. piped output) — ignore

from config import BotConfig
from core.binance_ws import BinanceWebSocketFeed
from core.dump_hedge_detector import DumpHedgeDetector, DumpHedgeSignal
from core.edge_detector import EdgeDetector, TradeSignal
from core.polymarket_client import PolymarketClient, MarketInfo
from core.polymarket_ws import PolymarketWSFeed
from integration.openclaw import OpenClawIntegration
from integration.telegram import TelegramAlerter
from risk.kelly import KellySizer
from risk.risk_manager import DumpHedgePosition, Position, RiskManager, TradingStatus
from utils.dashboard import render_dashboard
from utils.logger import get_logger, print_banner, setup_logging

logger = get_logger(__name__)


class PolymarketArbitrageBot:
    """
    Main orchestrator for the OpenClaw Polymarket Arb Bot.

    Supports three strategies (set via STRATEGY in .env):
      - latency_arb  : exploit ~2.7s Binance→Polymarket lag
      - dump_hedge   : buy YES+NO when combined ask < $1.00
      - both         : run both simultaneously

    Coordinates all subsystems:
      1. BinanceWebSocketFeed  — real-time asset prices (latency_arb only)
      2. EdgeDetector          — sigmoid model + 4-layer filter chain
      3. DumpHedgeDetector     — combined price scanner
      4. KellySizer            — position sizing (Kelly or fixed bet)
      5. RiskManager           — drawdown limits, daily halt, kill switch
      6. PolymarketClient      — CLOB order execution + market discovery
      7. OpenClawIntegration   — bidirectional AI agent communication
      8. TelegramAlerter       — real-time push notifications
    """

    LOOP_INTERVAL_SECONDS = 0.05  # Main loop frequency: 20 Hz (faster signal detection)
    POSITION_CHECK_INTERVAL = 0.5  # Check exit conditions every 0.5s (was 1.0s)
    COMMAND_POLL_INTERVAL = 10.0   # How often to poll OpenClaw for commands
    HEARTBEAT_INTERVAL = 2.0       # Dashboard refresh every 2s (prices update live)

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._running = False
        self._start_time = 0.0
        self._shutdown_requested = False   # set by SIGINT; triggers confirmation prompt
        self._last_position_check = 0.0
        self._last_command_poll = 0.0
        self._last_heartbeat = 0.0
        self._last_kill_log = 0.0
        self._last_idle_log = 0.0
        self._loop_count = 0

        # Per-asset position lock: asset → order_id of the current open position.
        # A new signal for an asset is blocked until its position is fully closed.
        self._asset_open_position: dict = {}  # e.g. {"btc": "0xabc...", "sol": None}
        # Separate lock for dump-hedge positions (asset → dh_id).
        self._asset_open_dh_position: dict = {}

        # Track consecutive sell failures per order_id.
        # After MAX_SELL_RETRIES failures a Telegram alert fires and we stop retrying.
        self._sell_fail_count: dict = {}
        self.MAX_SELL_RETRIES = 3

        # Track order_ids for which on-chain auto-redeem has been triggered.
        # Prevents calling redeem_positions more than once per position.
        self._auto_redeem_triggered: set = set()

        # Track position/dh_ids that have already received a near-timeout alert.
        # Cleared when the position closes so the set doesn't grow unbounded.
        self._timeout_alerted: set = set()

        # Tracks whether a "no markets found" Telegram alert was already sent per asset.
        # Resets when the market is found again so a follow-up alert fires if it recurs.
        self._market_empty_alerted: dict = {}  # asset → bool

        # Daily summary: track last UTC date the summary was sent (YYYY-MM-DD string).
        self._last_daily_summary_date: str = ""

        # Initialize all subsystems
        logger.info("Initializing bot subsystems... (strategy=%s)", config.strategy)

        # ── Binance feeds ─────────────────────────────────────────────────────
        # Only needed for latency-arb (requires real-time price to detect lag).
        # dump_hedge strategy works purely from Polymarket pricing — no Binance needed.
        _ASSET_SYMBOLS = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
        _use_binance = config.strategy in ("latency_arb", "both")
        if _use_binance:
            self._feeds: dict = {
                asset: BinanceWebSocketFeed(
                    symbol=_ASSET_SYMBOLS[asset],
                    reconnect_delay=config.binance_reconnect_delay,
                )
                for asset in config.trading_markets
                if asset in _ASSET_SYMBOLS
            }
            # Primary feed used for startup readiness check (prefer BTC, else first available)
            self.binance_feed = self._feeds.get("btc") or next(iter(self._feeds.values()))
        else:
            self._feeds = {}
            self.binance_feed = None
        # Convenience aliases used in a few places
        self.btc_feed = self._feeds.get("btc")
        self.eth_feed = self._feeds.get("eth")
        self.sol_feed = self._feeds.get("sol")

        logger.info(
            "Active markets: %s | Binance feeds: %s",
            ", ".join(config.trading_markets).upper(),
            "enabled" if _use_binance else "disabled (dump_hedge only)",
        )

        self.polymarket_client = PolymarketClient(
            host=config.polymarket_host,
            chain_id=config.polymarket_chain_id,
            private_key=config.polymarket_private_key,
            funder=config.polymarket_funder,
            signature_type=config.polymarket_signature_type,
            paper_mode=config.paper_mode,
            trade_window_minutes=config.trade_window_minutes,
            paper_slippage_pct=config.paper_slippage_pct if config.paper_mode else 0.0,
            proxy_url=config.proxy_url,
        )

        # ── Edge detector (latency-arb) ───────────────────────────────────────
        if config.strategy in ("latency_arb", "both"):
            self.edge_detector: Optional[EdgeDetector] = EdgeDetector(
                feeds=self._feeds,
                polymarket_client=self.polymarket_client,
                min_edge_threshold=config.edge_min_edge_threshold,
                lag_window_seconds=config.edge_lag_window_seconds,
                cooldown_seconds=config.edge_cooldown_seconds,
                min_market_liquidity=config.edge_min_market_liquidity,
                trade_window_minutes=config.trade_window_minutes,
                min_entry_price=config.edge_min_entry_price,
                max_entry_price=config.edge_max_entry_price,
                min_fair_value_strength=config.edge_min_fair_value_strength,
                min_seconds_remaining=config.edge_min_seconds_remaining,
            )
        else:
            self.edge_detector = None

        # ── Dump-hedge detector ───────────────────────────────────────────────
        if config.strategy in ("dump_hedge", "both"):
            self.dh_detector: Optional[DumpHedgeDetector] = DumpHedgeDetector(
                polymarket_client=self.polymarket_client,
                assets=config.trading_markets,
                sum_target=config.dh_sum_target,
                min_discount=config.dh_min_discount,
                min_market_liquidity=config.edge_min_market_liquidity,
                trade_window_minutes=config.trade_window_minutes,
                cooldown_seconds=config.dh_cooldown_seconds,
            )
        else:
            self.dh_detector = None

        # KELLY_ENABLED=true  → Kelly formula (fixed_bet_usdc=0 disables fixed mode)
        # KELLY_ENABLED=false → fixed bet (use RISK_FIXED_BET_USDC)
        _fixed_bet = 0.0 if config.kelly_enabled else config.risk_fixed_bet_usdc
        self.kelly_sizer = KellySizer(
            kelly_fraction=config.risk_kelly_fraction,
            max_position_fraction=config.risk_max_position_fraction,
            fixed_bet_usdc=_fixed_bet,
            adaptive_kelly_enabled=config.kelly_adaptive_enabled,
        )

        # Live balance fetched async in run() — use config default here
        starting_balance = config.paper_starting_balance
        self.risk_manager = RiskManager(
            starting_balance=starting_balance,
            max_position_fraction=config.risk_max_position_fraction,
            daily_loss_limit=config.risk_daily_loss_limit,
            total_drawdown_kill=config.risk_total_drawdown_kill,
            max_concurrent_positions=config.risk_max_concurrent_positions,
        )

        # Polymarket CLOB WebSocket feed (real-time price_change events)
        # Replaces REST polling for price lookups: ~10-50ms vs ~200-500ms
        self.pm_ws_feed = PolymarketWSFeed(
            on_price_change=self._on_pm_price_change,
            proxy_url=config.proxy_url,
        )
        # Attach WS feed to client so get_market_price() uses cache first
        self.polymarket_client.attach_ws_feed(self.pm_ws_feed)

        self.openclaw = OpenClawIntegration(
            api_key=config.openclaw_api_key,
            api_url=config.openclaw_api_url,
            agent_id=config.openclaw_agent_id,
            report_interval_seconds=config.openclaw_report_interval,
            enabled=config.openclaw_enabled,
        )

        self.telegram = TelegramAlerter(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            enabled=config.telegram_enabled,
            proxy_url=config.proxy_url,
        )

        # Register OpenClaw command handlers
        self._register_openclaw_commands()

        if config.proxy_url:
            _proxy_host = config.proxy_url.split("@")[-1] if "@" in config.proxy_url else config.proxy_url
            logger.info("Proxy enabled: %s (REST + WS + Telegram)", _proxy_host)
        logger.info(
            "Bot initialized | Mode: %s | Balance: $%.2f",
            "PAPER" if config.paper_mode else "LIVE",
            starting_balance,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the bot. Runs indefinitely until stopped."""
        self._running = True
        self._start_time = time.time()

        # Start Binance feeds in background (only when strategy needs them)
        feed_tasks = [asyncio.create_task(feed.start()) for feed in self._feeds.values()]

        # Start Polymarket CLOB WebSocket feed in background
        pm_ws_task = asyncio.create_task(self.pm_ws_feed.start())
        logger.info("Polymarket CLOB WebSocket feed starting...")

        # Wait for first Binance price tick — only required for latency-arb.
        # dump_hedge strategy works entirely from Polymarket prices and can start immediately.
        if self.binance_feed is not None:
            logger.info("Waiting for Binance price feed (REST bootstrap + WebSocket)...")
            for _ in range(350):  # Up to 35 seconds
                if self.binance_feed.latest_price is not None:
                    break
                await asyncio.sleep(0.1)

            if self.binance_feed.latest_price is None:
                logger.error(
                    "Failed to receive BTC price from Binance within 35 seconds. "
                    "Check your internet connection and firewall settings. "
                    "Ensure ports 9443 and 443 are not blocked."
                )
                self._running = False
                for t in feed_tasks:
                    t.cancel()
                return

            primary_asset = self.config.trading_markets[0].upper()
            logger.info(
                "Binance %s feed connected. %s: $%.2f",
                primary_asset, primary_asset, self.binance_feed.latest_price,
            )
        else:
            logger.info("Binance feeds skipped (strategy=%s)", self.config.strategy)
        # Sync live balance from blockchain before starting (live mode only)
        if not self.config.paper_mode:
            live_balance = await self.polymarket_client.get_portfolio_balance()
            if live_balance is not None and live_balance > 0:
                # Replace the entire baseline tracking to match the actual live balance
                # so that peak balance and drawdown calculations start from a true zero-point
                # and don't mistakenly compare against the paper configuration default.
                self.risk_manager.set_live_starting_balance(live_balance)
            else:
                logger.warning(
                    "Could not fetch live balance — using config default $%.2f. "
                    "Verify POLYMARKET_FUNDER address is correct.",
                    self.risk_manager.current_balance,
                )

        # Send startup notifications
        self.telegram.send_startup(
            paper_mode=self.config.paper_mode,
            balance=self.risk_manager.current_balance,
            strategy=self.config.strategy,
            markets=self.config.trading_markets,
        )
        cfg = self.config
        self.openclaw.push_startup_notification({
            "paper_mode": cfg.paper_mode,
            "strategy": cfg.strategy,
            "balance": self.risk_manager.current_balance,
            "markets": cfg.trading_markets,
            "trade_window_minutes": cfg.trade_window_minutes,
            # Latency-arb params (only relevant when strategy includes latency_arb)
            "min_edge_threshold": cfg.edge_min_edge_threshold,
            "lag_window_seconds": cfg.edge_lag_window_seconds,
            "kelly_fraction": cfg.risk_kelly_fraction,
            # Dump-hedge params (only relevant when strategy includes dump_hedge)
            "dh_sum_target": cfg.dh_sum_target,
            "dh_fixed_bet_usdc": cfg.dh_fixed_bet_usdc,
            "dh_early_exit_fraction": cfg.dh_early_exit_profit_fraction,
        })

        # Main trading loop
        logger.info("Entering main trading loop...")
        try:
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("Main loop cancelled.")
        finally:
            for feed in self._feeds.values():
                feed.stop()
            await self.pm_ws_feed.stop()
            for t in feed_tasks:
                t.cancel()
            pm_ws_task.cancel()
            # Await all cancelled tasks so their cleanup completes before
            # closing the HTTP client (prevents dangling requests mid-flight).
            await asyncio.gather(*feed_tasks, pm_ws_task, return_exceptions=True)
            # Export any trades that closed today but weren't yet written to CSV
            # (handles mid-day shutdowns; midnight trigger covers normal operation)
            _shutdown_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            try:
                self._export_trades_csv(_shutdown_date)
            except Exception as _exc:
                logger.warning("CSV export on shutdown failed: %s", _exc)

            self.telegram.send_message("🛑 Bot stopped.")
            await self.polymarket_client.close()
            self.telegram.close()
            logger.info("Bot shutdown complete.")

    async def _main_loop(self) -> None:
        """Core trading loop: evaluate edges, execute trades, manage positions."""
        while self._running:
            loop_start = time.time()

            # Ctrl+C confirmation — checked before any other logic so the
            # prompt appears on the very next loop iteration after SIGINT.
            if self._shutdown_requested:
                self._shutdown_requested = False
                confirmed = await self._confirm_shutdown()
                if confirmed:
                    self._running = False
                    break
                # User chose to continue — resume normally

            try:
                self._loop_count += 1
                now = time.time()

                # 1. Poll OpenClaw for commands
                if now - self._last_command_poll >= self.COMMAND_POLL_INTERVAL:
                    await self.openclaw.poll_and_execute_commands()
                    self._last_command_poll = now

                # 2. Check risk state
                if not self.risk_manager.is_trading_allowed:
                    status = self.risk_manager.status
                    if status == TradingStatus.KILLED:
                        # Log once, then only every 60s — avoids CRITICAL spam every second
                        if now - self._last_kill_log >= 60.0:
                            logger.critical(
                                "Kill switch active — bot is halted. "
                                "Balance: $%.2f | Use reset_kill_switch(confirm=True) to resume.",
                                self.risk_manager.current_balance,
                            )
                            self._last_kill_log = now

                    # Dashboard must refresh here too — the normal heartbeat below is
                    # never reached because of the `continue`, so status stays stale.
                    if now - self._last_heartbeat >= self.HEARTBEAT_INTERVAL:
                        render_dashboard(
                            feeds=self._feeds,
                            risk_state=self.risk_manager.get_state(),
                            open_positions=dict(self.risk_manager._open_positions),
                            asset_locks=dict(self._asset_open_position),
                            edge_stats=self.edge_detector.get_stats() if self.edge_detector else {},
                            active_markets={
                                **(dict(self.edge_detector._active_market_by_asset) if self.edge_detector else {}),
                                **(dict(self.dh_detector._active_market_by_asset) if self.dh_detector else {}),
                            },
                            edge_detector=self.edge_detector,
                            paper_mode=self.config.paper_mode,
                            log_lines=self.config.dashboard_log_lines,
                            trade_window_minutes=self.config.trade_window_minutes,
                            strategy=self.config.strategy,
                            open_dh_positions=dict(self.risk_manager._open_dh_positions),
                            dh_detector=self.dh_detector,
                            engine_config={
                                "dh_sum_target":            self.config.dh_sum_target,
                                "dh_min_discount":          self.config.dh_min_discount,
                                "dh_fixed_bet_usdc":        self.config.dh_fixed_bet_usdc,
                                "max_concurrent_positions": self.config.risk_max_concurrent_positions,
                                "daily_loss_limit":         self.config.risk_daily_loss_limit,
                            },
                            telegram_enabled=self.config.telegram_enabled,
                            uptime_s=now - self._start_time,
                        )
                        self._last_heartbeat = now

                    await asyncio.sleep(1.0)
                    continue

                # 3. Evaluate for edge signal (latency-arb)
                if self.edge_detector is not None:
                    signal = await self.edge_detector.evaluate()
                    if signal:
                        await self._handle_signal(signal)

                # 3b. Evaluate for dump-hedge signal
                if self.dh_detector is not None:
                    dh_signal = await self.dh_detector.evaluate()
                    if dh_signal:
                        await self._handle_dh_signal(dh_signal)

                # 3c. Alert if market discovery has been empty too long (both strategies failed)
                _EMPTY_ALERT_THRESHOLD = 10  # ~5 minutes at 30s cache TTL
                for _asset in self.config.trading_markets:
                    _empty = self.polymarket_client._consecutive_empty_by_asset.get(_asset, 0)
                    if _empty >= _EMPTY_ALERT_THRESHOLD and not self._market_empty_alerted.get(_asset):
                        logger.critical(
                            "[%s] Market discovery has returned empty %d consecutive times "
                            "— slug scan and Gamma search both failing. Check API connectivity.",
                            _asset.upper(), _empty,
                        )
                        self.telegram.send_risk_alert(
                            "MarketDiscoveryFailed",
                            f"[{_asset.upper()}] No active markets found for {_empty} consecutive "
                            f"fetches — both slug scan and Gamma search returning empty. "
                            f"Bot is NOT trading this asset. Check API connectivity.",
                            severity="CRITICAL",
                        )
                        self._market_empty_alerted[_asset] = True
                    elif _empty == 0:
                        # Market found again — reset alert so it fires again if it recurs
                        self._market_empty_alerted[_asset] = False

                # 3d. Periodic idle heartbeat — proves the bot is alive during quiet markets
                _IDLE_LOG_INTERVAL = 60.0
                if now - self._last_idle_log >= _IDLE_LOG_INTERVAL:
                    self._last_idle_log = now
                    rs = self.risk_manager.get_state()
                    market_status = " | ".join(
                        f"{a.upper()}: {'OK' if self.polymarket_client._consecutive_empty_by_asset.get(a, 0) == 0 else 'no market'}"
                        for a in self.config.trading_markets
                    )
                    open_pos = rs.open_positions + rs.open_dh_positions
                    logger.info(
                        "[heartbeat] alive | %s | open=%d | balance=$%.2f | trades=%d | uptime=%.0fm",
                        market_status,
                        open_pos,
                        rs.current_balance,
                        rs.total_trades + rs.total_dh_trades,
                        (now - self._start_time) / 60,
                    )

                # 3e. Daily summary at midnight UTC (once per calendar day)
                _today_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d")
                if _today_utc != self._last_daily_summary_date and self._last_daily_summary_date:
                    self.telegram.send_daily_summary(
                        risk_state=self.risk_manager.get_state(),
                        uptime_seconds=now - self._start_time,
                    )
                    self._export_trades_csv(self._last_daily_summary_date)
                    self._last_daily_summary_date = _today_utc
                elif not self._last_daily_summary_date:
                    self._last_daily_summary_date = _today_utc

                # 4. Periodically check open positions for exit conditions
                if now - self._last_position_check >= self.POSITION_CHECK_INTERVAL:
                    await self._check_open_positions()
                    if self.dh_detector is not None:
                        await self._check_open_dh_positions()
                    self._last_position_check = now

                # 5. Push performance summary to OpenClaw
                risk_state = self.risk_manager.get_state()
                edge_stats = self.edge_detector.get_stats() if self.edge_detector else {}
                dh_stats = self.dh_detector.get_stats() if self.dh_detector else {}
                self.openclaw.push_performance_summary(
                    risk_state=risk_state,
                    edge_stats={**edge_stats, **dh_stats},
                    feed_stats=self.binance_feed.get_stats() if self.binance_feed else {},
                )

                # 6. Dashboard refresh
                if now - self._last_heartbeat >= self.HEARTBEAT_INTERVAL:
                    edge_stats = self.edge_detector.get_stats() if self.edge_detector else {}

                    # Refresh active markets and subscribe PM WS for all active assets
                    subs = getattr(self, "_pm_ws_subscribed_conditions", set())
                    if self.edge_detector is not None:
                        for asset in self.config.trading_markets:
                            mkt = await self.edge_detector._get_active_5m_market(asset)
                            if mkt and self.pm_ws_feed.is_connected and mkt.condition_id not in subs:
                                self._subscribe_pm_ws_to_market(mkt)
                                subs.add(mkt.condition_id)
                    self._pm_ws_subscribed_conditions = subs

                    render_dashboard(
                        feeds=self._feeds,
                        risk_state=self.risk_manager.get_state(),
                        open_positions=dict(self.risk_manager._open_positions),
                        asset_locks=dict(self._asset_open_position),
                        edge_stats=edge_stats,
                        active_markets={
                            **(dict(self.edge_detector._active_market_by_asset) if self.edge_detector else {}),
                            **(dict(self.dh_detector._active_market_by_asset) if self.dh_detector else {}),
                        },
                        edge_detector=self.edge_detector,
                        paper_mode=self.config.paper_mode,
                        log_lines=self.config.dashboard_log_lines,
                        trade_window_minutes=self.config.trade_window_minutes,
                        strategy=self.config.strategy,
                        open_dh_positions=dict(self.risk_manager._open_dh_positions),
                        dh_detector=self.dh_detector,
                        engine_config={
                            "dh_sum_target":            self.config.dh_sum_target,
                            "dh_min_discount":          self.config.dh_min_discount,
                            "dh_fixed_bet_usdc":        self.config.dh_fixed_bet_usdc,
                            "max_concurrent_positions": self.config.risk_max_concurrent_positions,
                            "daily_loss_limit":         self.config.risk_daily_loss_limit,
                        },
                        telegram_enabled=self.config.telegram_enabled,
                        uptime_s=now - self._start_time,
                    )
                    self._last_heartbeat = now

            except Exception as exc:
                logger.error("Unexpected error in main loop: %s", exc, exc_info=True)
                self.telegram.send_risk_alert(
                    "UnexpectedError",
                    str(exc),
                    severity="ERROR",
                )

            # Maintain loop frequency
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, self.LOOP_INTERVAL_SECONDS - elapsed)
            await asyncio.sleep(sleep_time)

    def stop(self) -> None:
        """Gracefully stop the bot."""
        logger.info("Shutdown requested.")
        self._running = False
        for feed in self._feeds.values():
            feed.stop()

    async def _confirm_shutdown(self) -> bool:
        """
        Pause the dashboard, warn the user about open positions, and ask
        whether to exit or continue.

        Returns True  → caller should stop the bot.
        Returns False → user chose to continue; caller resumes the loop.
        """
        from utils.dashboard import _stop_live

        la_count = len(self.risk_manager._open_positions)
        dh_count = len(self.risk_manager._open_dh_positions)
        total    = la_count + dh_count

        # Pause the Rich Live TUI so we can write to stdout cleanly.
        _stop_live()

        LINE = "─" * 57

        if total > 0:
            print(f"\n┌{LINE}┐")
            print(f"│  ⚠  CTRL+C — OPEN POSITIONS DETECTED                  │")
            print(f"├{LINE}┤")
            print(f"│  Latency-Arb  :  {la_count:<3}  position(s)                    │")
            print(f"│  Dump-Hedge   :  {dh_count:<3}  position(s)                    │")
            print(f"│  Total open   :  {total:<3}                                │")
            print(f"├{LINE}┤")
            print(f"│  [E] + ENTER  →  exit  (positions left unresolved)    │")
            print(f"│  [C] + ENTER  →  continue running                     │")
            print(f"│  [D] + ENTER  →  drain mode (no new trades, wait)     │")
            print(f"└{LINE}┘")
        else:
            print(f"\n┌{LINE}┐")
            print(f"│  CTRL+C — no open positions · shutting down…            │")
            print(f"└{LINE}┘")
            return True

        try:
            raw = await asyncio.get_running_loop().run_in_executor(
                None, lambda: input("Your choice [E/C/D]: ").strip().lower()
            )
            if raw.startswith("d"):
                self.risk_manager.pause("Drain Mode active — halting new trades")
                print("\nDrain Mode activated. Bot will continue running to close open positions.")
                return False
            return raw.startswith("e")
        except (EOFError, KeyboardInterrupt):
            # Second Ctrl+C while prompt is showing → force exit
            print("\nForce-exit.")
            return True

    # ─────────────────────────────────────────────────────────────────────────
    # Signal Handling & Trade Execution
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_signal(self, trade_signal: TradeSignal) -> None:
        """Process a trade signal: size, validate, and execute."""
        # Block if this asset already has an open (unresolved) position.
        existing = self._asset_open_position.get(trade_signal.asset)
        if existing:
            logger.debug(
                "Asset %s locked by open position %s — new signal ignored",
                trade_signal.asset.upper(), existing[:12],
            )
            return

        logger.info("Processing signal: %s", trade_signal)

        # NORMAL MODE: buy in the direction of the signal.
        # Signal says UP → buy YES, DOWN → buy NO.
        # Use trade_signal.current_polymarket_price (fresh price fetched by edge detector)
        # NOT market.yes_price/no_price which may be stale/default 0.50.
        if trade_signal.direction == "UP":
            trade_token_id    = trade_signal.market.yes_token_id
        else:
            trade_token_id    = trade_signal.market.no_token_id
        trade_entry_price = trade_signal.current_polymarket_price

        # Calculate Kelly position size
        win_prob = trade_signal.fair_value_estimate
        logger.info(
            "Kelly inputs: balance=$%.2f win_prob=%.3f entry_price=%.4f token=%s",
            self.risk_manager.current_balance, win_prob, trade_entry_price,
            trade_token_id[:16] if trade_token_id else "None",
        )
        kelly = self.kelly_sizer.calculate(
            bankroll=self.risk_manager.current_balance,
            win_probability=win_prob,
            current_price=trade_entry_price,
            historical_win_rate=self.risk_manager.win_rate or None,
        )

        if kelly is None:
            logger.warning("Kelly returned None — check inputs above.")
            return

        # Risk check
        allowed, reason = self.risk_manager.can_open_position(kelly.position_size_usdc)
        if not allowed:
            logger.warning("Position blocked by risk manager: %s", reason)
            return

        # Execute the order
        order_result = await self.polymarket_client.place_market_order(
            token_id=trade_token_id,
            side="BUY",
            amount_usdc=kelly.position_size_usdc,
            market_info=trade_signal.market,
        )

        if not order_result.success:
            logger.error("Order failed: %s", order_result.error)
            self.telegram.send_risk_alert(
                "OrderFailed",
                f"Order error: {order_result.error}",
                severity="ERROR",
            )
            # Set per-asset cooldown so we don't immediately retry the same asset
            self.edge_detector.reset_cooldown(trade_signal.asset)
            return

        # Acquire per-asset lock so no second order is placed until this closes.
        self._asset_open_position[trade_signal.asset] = order_result.order_id

        # Register position with risk manager
        position = Position(
            order_id=order_result.order_id,
            token_id=trade_token_id,
            market_question=trade_signal.market.question,
            side="BUY",
            entry_price=order_result.price,
            size_shares=order_result.size,
            cost_usdc=order_result.size * order_result.price,
            opened_at=order_result.timestamp,
            asset=trade_signal.asset,
            direction=trade_signal.direction,  # "UP" or "DOWN"
            condition_id=trade_signal.market.condition_id,
            paper_mode=self.config.paper_mode,
        )
        self.risk_manager.register_trade_open(position)

        # Notifications
        self.telegram.send_trade_opened(
            order_id=order_result.order_id,
            market_question=trade_signal.market.question,
            side=trade_signal.side,
            price=order_result.price,
            size_usdc=kelly.position_size_usdc,
            edge=trade_signal.edge,
            btc_move=trade_signal.btc_move,
            paper_mode=self.config.paper_mode,
            asset=trade_signal.asset,
            direction=trade_signal.direction,
        )
        self.openclaw.push_trade_opened(
            order_id=order_result.order_id,
            market_question=trade_signal.market.question,
            side=trade_signal.side,
            price=order_result.price,
            size_usdc=kelly.position_size_usdc,
            edge=trade_signal.edge,
            paper_mode=self.config.paper_mode,
        )

        logger.info(
            "Trade executed: %s | $%.2f USDC | Edge: %.4f | Kelly: %.4f",
            order_result.order_id,
            kelly.position_size_usdc,
            trade_signal.edge,
            kelly.fractional_kelly,
        )

    async def _check_open_positions(self) -> None:
        open_orders = self.polymarket_client.get_open_orders()
        # CLOB API returns "orderID"; tolerate "id" and "orderId" variants too.
        open_order_ids = {
            o.get("orderID") or o.get("orderId") or o.get("id")
            for o in open_orders
        } - {None}

        for order_id, position in list(self.risk_manager._open_positions.items()):

            # Skip if the entry order is still pending in the order book
            # (not yet filled — no position to sell yet)
            if order_id in open_order_ids:
                logger.debug(
                    "Position %s still pending in order book — skipping exit check.",
                    order_id[:20],
                )
                continue

            current_price = await self.polymarket_client.get_market_price(
                position.token_id, "SELL"
            )

            if current_price is None:
                age = time.time() - position.opened_at
                if self.config.paper_mode:
                    if age >= 30.0:
                        logger.info("Position %s: prices unavailable after %.0fs (paper mode) — closing as resolved", order_id[:20], age)
                        closed = self.risk_manager.register_trade_close(
                            order_id=order_id,
                            exit_price=position.entry_price,
                            actual_proceeds_usdc=None,
                        )
                        if closed:
                            self._sell_fail_count.pop(order_id, None)
                            self._timeout_alerted.discard(order_id)
                            if position.asset:
                                self._asset_open_position.pop(position.asset, None)
                                if self.edge_detector:
                                    self.edge_detector.reset_cooldown(position.asset)
                            
                            self.telegram.send_trade_closed(
                                order_id=order_id,
                                pnl_usdc=closed.pnl_usdc,
                                exit_price=position.entry_price,
                                duration_seconds=closed.duration_seconds,
                                paper_mode=self.config.paper_mode,
                                entry_price=position.entry_price,
                                size_usdc=position.cost_usdc,
                                exit_reason="Market resolved (prices unavailable)",
                                asset=position.asset,
                                direction=position.direction,
                            )
                            if closed.pnl_usdc is not None:
                                self.openclaw.push_trade_closed(
                                    order_id=order_id,
                                    pnl_usdc=closed.pnl_usdc,
                                    exit_price=position.entry_price,
                                    duration_seconds=closed.duration_seconds,
                                    paper_mode=self.config.paper_mode,
                                )
                else:
                    past_resolution = age > self.config.position_timeout_seconds + 30
                    if position.condition_id and past_resolution and order_id not in self._auto_redeem_triggered:
                        asyncio.create_task(self._attempt_auto_redeem(position))
                continue

            should_exit = False
            exit_reason = ""

            entry_price = position.entry_price
            current_pnl_pct = (current_price - entry_price) / entry_price

            cfg = self.config
            # Priority order: near-resolution exits first (market about to settle),
            # then take profit / stop loss, then timeout.
            if current_price >= cfg.near_win_price:
                should_exit = True
                exit_reason = f"Near resolution YES: price={current_price:.3f} >= {cfg.near_win_price}"
            elif current_price <= cfg.near_loss_price:
                should_exit = True
                exit_reason = f"Near resolution NO: price={current_price:.3f} <= {cfg.near_loss_price}"
            elif current_price >= cfg.take_profit_price or current_pnl_pct >= cfg.take_profit_pnl:
                should_exit = True
                exit_reason = f"Take profit: price={current_price:.3f} pnl={current_pnl_pct:+.1%}"
            elif cfg.stop_loss_pnl < 0 and current_pnl_pct <= cfg.stop_loss_pnl:
                should_exit = True
                exit_reason = f"Stop loss: pnl={current_pnl_pct:+.1%} limit={cfg.stop_loss_pnl:+.1%}"
            elif time.time() - position.opened_at > cfg.position_timeout_seconds:
                should_exit = True
                exit_reason = f"Position timeout ({cfg.position_timeout_seconds:.0f}s)"

            # Warn once when position reaches 80% of timeout without triggering exit
            if not should_exit:
                age = time.time() - position.opened_at
                if (
                    age >= cfg.position_timeout_seconds * 0.80
                    and order_id not in self._timeout_alerted
                ):
                    self._timeout_alerted.add(order_id)
                    remaining = cfg.position_timeout_seconds - age
                    self.telegram.send_risk_alert(
                        "PositionNearTimeout",
                        f"Position {order_id[:12]} open {age:.0f}s — "
                        f"force-close in ~{remaining:.0f}s. "
                        f"Market: {position.market_question[:50]}",
                        severity="WARNING",
                    )

            if should_exit:
                # ── Sell-retry gate ──────────────────────────────────────────
                fail_count = self._sell_fail_count.get(order_id, 0)
                if fail_count >= self.MAX_SELL_RETRIES:
                    # Sell exhausted — attempt on-chain redeem once the market
                    # has had time to resolve (position age > timeout + 30s grace).
                    past_resolution = (
                        time.time() - position.opened_at
                        > self.config.position_timeout_seconds + 30
                    )
                    if (
                        not self.config.paper_mode
                        and position.condition_id
                        and past_resolution
                        and order_id not in self._auto_redeem_triggered
                    ):
                        asyncio.create_task(self._attempt_auto_redeem(position))
                    else:
                        logger.debug(
                            "Skipping sell for %s — max retries (%d) reached.",
                            order_id[:20], self.MAX_SELL_RETRIES,
                        )
                    continue

                logger.info("Closing position %s: %s", order_id[:20], exit_reason)

                # Step 1: Submit SELL order to Polymarket
                sell_result = await self.polymarket_client.place_market_order(
                    token_id=position.token_id,
                    side="SELL",
                    amount_usdc=position.size_shares,
                )

                # Step 2: Handle sell failure with retry counting
                if not sell_result.success:
                    new_count = fail_count + 1
                    self._sell_fail_count[order_id] = new_count
                    logger.error(
                        "SELL FAILED for %s | Attempt %d/%d | Error: %s",
                        order_id[:20], new_count, self.MAX_SELL_RETRIES, sell_result.error,
                    )
                    if new_count >= self.MAX_SELL_RETRIES:
                        # Exhausted all retries — alert operator
                        self.telegram.send_risk_alert(
                            "MaxSellRetriesReached",
                            f"Failed to sell position {order_id[:12]} after "
                            f"{self.MAX_SELL_RETRIES} attempts. "
                            f"Please check manually on Polymarket!\n"
                            f"Last error: {sell_result.error}",
                            severity="CRITICAL",
                        )
                        logger.critical(
                            "MAX SELL RETRIES (%d) REACHED for %s — "
                            "manual intervention required!",
                            self.MAX_SELL_RETRIES, order_id[:20],
                        )
                    continue  # do not update state — retry on next iteration

                # Step 3: Update internal state with actual fill price
                # actual_proceeds = real cash received from sell fill
                # (on-chain shares × fill price) — not the estimated entry shares.
                actual_exit_price = sell_result.price
                actual_proceeds = sell_result.size * sell_result.price if not self.config.paper_mode else None
                closed = self.risk_manager.register_trade_close(
                    order_id=order_id,
                    exit_price=actual_exit_price,
                    actual_proceeds_usdc=actual_proceeds,
                )

                if closed is None:
                    # register_trade_close rejected (e.g. invalid price) — keep position tracked
                    logger.error(
                        "register_trade_close rejected for %s — position still tracked.",
                        order_id[:20],
                    )
                    continue

                # Sell succeeded — clear retry counter, timeout alert state, and asset lock.
                # Use position.asset directly (reliable) instead of scanning by order_id.
                self._sell_fail_count.pop(order_id, None)
                self._timeout_alerted.discard(order_id)
                if position.asset:
                    self._asset_open_position.pop(position.asset, None)
                    logger.info("Asset lock released: %s", position.asset.upper())
                    # Reset per-asset cooldown from close time so the bot waits
                    # a full cooldown_seconds before re-entering this asset.
                    if self.edge_detector is not None:
                        self.edge_detector.reset_cooldown(position.asset)

                # Always notify on close — pnl_usdc may be None in paper mode
                # but the trade still happened and should be reported.
                self.telegram.send_trade_closed(
                    order_id=order_id,
                    pnl_usdc=closed.pnl_usdc,
                    exit_price=actual_exit_price,
                    duration_seconds=closed.duration_seconds,
                    paper_mode=self.config.paper_mode,
                    entry_price=position.entry_price,
                    size_usdc=position.cost_usdc,
                    exit_reason=exit_reason,
                    asset=position.asset,
                    direction=position.direction,
                )
                if closed.pnl_usdc is not None:
                    self.openclaw.push_trade_closed(
                        order_id=order_id,
                        pnl_usdc=closed.pnl_usdc,
                        exit_price=actual_exit_price,
                        duration_seconds=closed.duration_seconds,
                        paper_mode=self.config.paper_mode,
                    )

                if self.risk_manager.status == TradingStatus.KILLED:
                    reason = self.risk_manager._kill_reason or "Unknown"
                    self.telegram.send_kill_switch_alert(
                        reason,
                        risk_state=self.risk_manager.get_state(),
                    )
                    self.openclaw.push_kill_switch_alert(reason)

                risk_state = self.risk_manager.get_state()
                if risk_state.circuit_breaker_active:
                    resume_str = datetime.datetime.utcfromtimestamp(
                        risk_state.circuit_breaker_resume_at
                    ).strftime("%H:%M:%S UTC")
                    self.telegram.send_risk_alert(
                        "CircuitBreaker",
                        f"Circuit breaker triggered — trading paused until {resume_str}. "
                        f"Reason: {self.risk_manager._kill_reason}",
                        severity="WARNING",
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Dump-Hedge Execution
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_dh_signal(self, signal: DumpHedgeSignal) -> None:
        """Open a two-leg dump-hedge position: buy both YES and NO simultaneously."""
        asset = signal.asset

        # Block if this asset already has an open DH position.
        # Use "PENDING" as an early sentinel so the 20Hz loop cannot re-enter
        # between the first order submission and the final lock assignment.
        if self._asset_open_dh_position.get(asset):
            logger.debug(
                "Asset %s already has open DH position %s — signal ignored",
                asset.upper(), self._asset_open_dh_position[asset],
            )
            return
        self._asset_open_dh_position[asset] = "PENDING"

        cfg = self.config
        POLYMARKET_MIN_LEG_USDC = 1.00
        # Tolerance for floating-point comparisons (e.g. 1.0/0.47*0.47 = 0.9999...).
        FLOAT_TOL = 1e-6

        # Use signal prices directly — they are already fresh REST prices fetched
        # by the detector moments ago. A second re-fetch adds ~600ms latency (two
        # more REST round-trips) which consistently sees the market back at ~0.99,
        # rejecting real edges. With DH_SUM_TARGET=0.93 (7% margin), signal prices
        # are reliable enough; actual fill prices may be slightly worse but still
        # within the margin.
        exec_yes      = signal.yes_price
        exec_no       = signal.no_price
        exec_combined = signal.combined_price

        # Calculate sizing so both legs receive the SAME share count.
        yes_min = POLYMARKET_MIN_LEG_USDC / exec_yes
        no_min  = POLYMARKET_MIN_LEG_USDC / exec_no
        size_from_min    = max(yes_min, no_min)
        size_from_budget = cfg.dh_fixed_bet_usdc / exec_combined
        size_shares      = min(size_from_min, size_from_budget)

        combined_cost = exec_combined * size_shares
        exec_discount = 1.0 - exec_combined
        locked_profit = exec_discount * size_shares

        # USDC amounts per leg — derived from the SAME size_shares and signal prices.
        yes_usdc = size_shares * exec_yes
        no_usdc  = size_shares * exec_no

        # Guard: budget can't cover the $1.00 minimum per leg.
        # Use FLOAT_TOL to absorb floating-point rounding (1.0/p*p ≈ 0.9999...).
        if yes_usdc < POLYMARKET_MIN_LEG_USDC - FLOAT_TOL or no_usdc < POLYMARKET_MIN_LEG_USDC - FLOAT_TOL:
            logger.warning(
                "DH signal skipped — budget $%.2f too small for $1.00 min per leg "
                "(need ≥$%.2f combined). Raise DH_FIXED_BET_USDC.",
                cfg.dh_fixed_bet_usdc,
                (POLYMARKET_MIN_LEG_USDC / exec_yes + POLYMARKET_MIN_LEG_USDC / exec_no) * exec_combined,
            )
            self._asset_open_dh_position.pop(asset, None)
            return

        # Risk check
        allowed, reason = self.risk_manager.can_open_dh_position(combined_cost)
        if not allowed:
            logger.warning("DH position blocked by risk manager: %s", reason)
            self._asset_open_dh_position.pop(asset, None)
            return

        dh_id = f"dh_{asset}_{uuid.uuid4().hex[:8]}"

        logger.info(
            "Opening DH position %s | %s | YES@%.3f + NO@%.3f | "
            "%.4f shares | Cost: $%.2f | Locked profit: $%.2f",
            dh_id, asset.upper(), exec_yes, exec_no,
            size_shares, combined_cost, locked_profit,
        )

        # Place YES (BUY) leg — amount sized from fresh exec price
        yes_result = await self.polymarket_client.place_market_order(
            token_id=signal.yes_token_id,
            side="BUY",
            amount_usdc=yes_usdc,
            market_info=signal.market,
        )
        if not yes_result.success or yes_result.size < 0.01:
            err = yes_result.error or f"Partial fill too small ({yes_result.size:.4f} shares)"
            logger.error("DH YES leg failed or missed liquidity: %s", err)
            self._asset_open_dh_position.pop(asset, None)
            self.dh_detector.reset_cooldown(asset)
            return

        # Place NO (BUY) leg — same size_shares, amount from fresh exec price
        no_result = await self.polymarket_client.place_market_order(
            token_id=signal.no_token_id,
            side="BUY",
            amount_usdc=no_usdc,
            market_info=signal.market,
        )
        if not no_result.success or no_result.size < 0.01:
            err = no_result.error or f"Partial fill too small ({no_result.size:.4f} shares)"
            logger.error(
                "DH NO leg failed or missed liquidity after YES filled: %s — attempting to sell YES position.",
                err,
            )
            # Try to recover by selling the YES position so capital is returned.
            yes_sell_result = await self.polymarket_client.place_market_order(
                token_id=signal.yes_token_id,
                side="SELL",
                amount_usdc=yes_result.size,
                market_info=signal.market,
            )
            if yes_sell_result.success:
                logger.warning(
                    "DH YES leg sold back successfully after NO leg failure. "
                    "Position unwound. YES order: %s",
                    yes_result.order_id,
                )
                self.telegram.send_risk_alert(
                    "DHNoLegFailed",
                    f"DH NO leg failed for {asset.upper()} — YES position sold back. "
                    f"YES order: {yes_result.order_id}. NO error: {no_result.error}",
                    severity="WARNING",
                )
            else:
                # Sell-back also failed — YES position is live and untracked.
                # Deduct its cost from internal balance so subsequent risk checks
                # are not based on an overstated balance.
                yes_cost = yes_result.size * yes_result.price
                self.risk_manager.update_balance(self.risk_manager._current_balance - yes_cost)
                logger.critical(
                    "DH YES sell-back FAILED — unhedged YES position on Polymarket! "
                    "Balance adjusted by -$%.2f. Manual close required. "
                    "YES order: %s | Sell error: %s",
                    yes_cost, yes_result.order_id, yes_sell_result.error,
                )
                self.telegram.send_risk_alert(
                    "DHNoLegFailed",
                    f"UNHEDGED YES position for {asset.upper()}! "
                    f"YES order {yes_result.order_id} is live on Polymarket. "
                    f"NO error: {no_result.error}. Sell-back error: {yes_sell_result.error}. "
                    f"Close manually!",
                    severity="CRITICAL",
                )
            self._asset_open_dh_position.pop(asset, None)
            self.dh_detector.reset_cooldown(asset)
            return

        # Promote PENDING sentinel to the real dh_id
        self._asset_open_dh_position[asset] = dh_id

        # Use actual fill prices and actual matched sizes.
        # DH requires equal shares. If partial fills differ, take the minimum to ensure a safe hedge.
        actual_combined = yes_result.price + no_result.price
        actual_size = min(yes_result.size, no_result.size)
        
        # ── Fix for Order Size Desync ──
        # If one leg partially filled less than the other, we MUST sell the excess
        # shares of the overfilled leg to remain perfectly hedged.
        yes_excess = yes_result.size - actual_size
        if yes_excess >= 0.01:
            logger.warning(
                "DH leg mismatch! YES overfilled by %.4f shares (YES=%.4f, NO=%.4f). Selling excess.", 
                yes_excess, yes_result.size, no_result.size
            )
            # Send alert to user
            self.telegram.send_risk_alert(
                "DHSizingMismatch",
                f"Partial fill mismatch on {asset.upper()}: YES={yes_result.size:.2f}, NO={no_result.size:.2f}. "
                f"Selling {yes_excess:.2f} excess YES shares to balance hedge.",
                severity="WARNING"
            )
            await self.polymarket_client.place_market_order(
                token_id=signal.yes_token_id,
                side="SELL",
                amount_usdc=yes_excess,
                market_info=signal.market,
            )

        no_excess = no_result.size - actual_size
        if no_excess >= 0.01:
            logger.warning(
                "DH leg mismatch! NO overfilled by %.4f shares (YES=%.4f, NO=%.4f). Selling excess.", 
                no_excess, yes_result.size, no_result.size
            )
            # Send alert to user
            self.telegram.send_risk_alert(
                "DHSizingMismatch",
                f"Partial fill mismatch on {asset.upper()}: YES={yes_result.size:.2f}, NO={no_result.size:.2f}. "
                f"Selling {no_excess:.2f} excess NO shares to balance hedge.",
                severity="WARNING"
            )
            await self.polymarket_client.place_market_order(
                token_id=signal.no_token_id,
                side="SELL",
                amount_usdc=no_excess,
                market_info=signal.market,
            )

        if actual_size < 0.01:
            logger.error("DH sizing failed (actual_size < 0.01). Aborting.")
            self._asset_open_dh_position.pop(asset, None)
            return
            
        position = DumpHedgePosition(
            dh_id=dh_id,
            yes_order_id=yes_result.order_id,
            no_order_id=no_result.order_id,
            yes_token_id=signal.yes_token_id,
            no_token_id=signal.no_token_id,
            market_question=signal.market.question,
            asset=asset,
            yes_entry_price=yes_result.price,
            no_entry_price=no_result.price,
            combined_entry_price=actual_combined,
            size_shares=actual_size,
            combined_cost_usdc=actual_combined * actual_size,
            locked_profit_usdc=(1.0 - actual_combined) * actual_size,
            opened_at=time.time(),
            paper_mode=cfg.paper_mode,
        )
        self.risk_manager.register_dh_open(position)

        actual_locked_pct = position.locked_profit_usdc / position.combined_cost_usdc if position.combined_cost_usdc > 0 else 0.0
        self.telegram.send_message(
            f"{'📄' if cfg.paper_mode else '🔒'} *DH Opened* | {asset.upper()} | "
            f"YES@{yes_result.price:.3f} + NO@{no_result.price:.3f} | "
            f"Combined: {position.combined_entry_price:.3f} | "
            f"Locked: ${position.locked_profit_usdc:.2f} ({actual_locked_pct:.1%} ROI) | "
            f"{'PAPER' if cfg.paper_mode else 'LIVE'}"
        )
        self.openclaw.push_dh_opened(
            dh_id=dh_id,
            asset=asset,
            yes_price=yes_result.price,
            no_price=no_result.price,
            combined_price=position.combined_entry_price,
            locked_profit_usdc=position.locked_profit_usdc,
            size_shares=position.size_shares,
            combined_cost_usdc=position.combined_cost_usdc,
            paper_mode=cfg.paper_mode,
        )

    async def _check_open_dh_positions(self) -> None:
        """Check DH positions for early-exit or timeout, then close if triggered."""
        cfg = self.config

        for dh_id, position in list(self.risk_manager._open_dh_positions.items()):
            age = time.time() - position.opened_at

            # Get current SELL prices for both legs
            yes_sell = await self.polymarket_client.get_market_price(
                position.yes_token_id, "SELL"
            )
            no_sell = await self.polymarket_client.get_market_price(
                position.no_token_id, "SELL"
            )

            # If prices unavailable (404 = market resolved/expired), force-close
            # at locked profit. When a binary market resolves, one leg pays $1.00
            # and the other $0.00 → net PnL = locked_profit regardless of direction.
            if yes_sell is None or no_sell is None:
                if age >= 30.0:  # Give 30s grace period before assuming resolved
                    logger.info(
                        "DH %s: prices unavailable after %.0fs — market likely resolved, "
                        "closing at locked profit $%.2f",
                        dh_id, age, position.locked_profit_usdc,
                    )
                    yes_exit = position.yes_entry_price
                    no_exit  = position.no_entry_price
                    # Use locked_profit as the PnL (structural guarantee at resolution)
                    actual_proceeds = position.combined_cost_usdc + position.locked_profit_usdc
                    closed = self.risk_manager.register_dh_close(
                        dh_id=dh_id,
                        yes_exit_price=yes_exit,
                        no_exit_price=no_exit,
                        exit_reason="Market resolved (prices unavailable)",
                        actual_proceeds_usdc=actual_proceeds,
                    )
                    if closed:
                        self._asset_open_dh_position.pop(position.asset, None)
                        if self.dh_detector:
                            self.dh_detector.reset_cooldown(position.asset)
                        self.telegram.send_message(
                            f"{'📄' if cfg.paper_mode else '✅'} *DH Resolved* | {position.asset.upper()} | "
                            f"PnL: ${position.locked_profit_usdc:+.2f} | Market resolved | "
                            f"Duration: {closed.duration_seconds:.0f}s"
                        )
                        self.openclaw.push_dh_closed(
                            dh_id=dh_id,
                            asset=position.asset,
                            pnl_usdc=closed.pnl_usdc or position.locked_profit_usdc,
                            exit_reason="Market resolved (prices unavailable)",
                            duration_seconds=closed.duration_seconds,
                            paper_mode=cfg.paper_mode,
                        )
                continue

            combined_sell = yes_sell + no_sell
            # Refresh age after the two await price-fetch calls for accuracy
            age = time.time() - position.opened_at

            should_exit = False
            exit_reason = ""

            # Early-exit: realised fraction of locked profit is good enough
            profit_so_far = (combined_sell - position.combined_entry_price) * position.size_shares
            target_profit = position.locked_profit_usdc * cfg.dh_early_exit_profit_fraction
            if profit_so_far >= target_profit:
                should_exit = True
                exit_reason = (
                    f"Early exit: profit ${profit_so_far:.2f} >= "
                    f"{cfg.dh_early_exit_profit_fraction:.0%} of locked ${position.locked_profit_usdc:.2f}"
                )

            # Stop loss: combined price moved against us beyond STOP_LOSS_PNL threshold
            elif cfg.stop_loss_pnl < 0 and position.combined_cost_usdc > 0:
                pnl_pct = profit_so_far / position.combined_cost_usdc
                if pnl_pct <= cfg.stop_loss_pnl:
                    should_exit = True
                    exit_reason = (
                        f"DH stop loss: pnl={pnl_pct:+.1%} limit={cfg.stop_loss_pnl:+.1%}"
                    )

            # Timeout
            elif age >= cfg.dh_timeout_seconds:
                should_exit = True
                exit_reason = f"DH timeout ({cfg.dh_timeout_seconds:.0f}s)"

            # Warn once when DH position reaches 80% of timeout without triggering exit
            if not should_exit:
                if (
                    age >= cfg.dh_timeout_seconds * 0.80
                    and dh_id not in self._timeout_alerted
                ):
                    self._timeout_alerted.add(dh_id)
                    remaining = cfg.dh_timeout_seconds - age
                    self.telegram.send_risk_alert(
                        "DHNearTimeout",
                        f"DH {dh_id[:12]} ({position.asset.upper()}) open {age:.0f}s — "
                        f"force-close in ~{remaining:.0f}s. "
                        f"Profit so far: ${profit_so_far:.2f}",
                        severity="WARNING",
                    )

            if not should_exit:
                continue

            logger.info("Closing DH %s: %s", dh_id, exit_reason)

            # Sell YES leg
            yes_sell_result = await self.polymarket_client.place_market_order(
                token_id=position.yes_token_id,
                side="SELL",
                amount_usdc=position.size_shares,
            )
            # Sell NO leg
            no_sell_result = await self.polymarket_client.place_market_order(
                token_id=position.no_token_id,
                side="SELL",
                amount_usdc=position.size_shares,
            )

            yes_ok = yes_sell_result.success
            no_ok  = no_sell_result.success

            if not cfg.paper_mode and not (yes_ok and no_ok):
                # At least one leg failed — tokens may still be held on-chain.
                # Do NOT close the position with partial proceeds; leave it open
                # so the next loop iteration retries the failing leg.
                if not yes_ok and not no_ok:
                    logger.critical(
                        "DH BOTH LEGS FAILED to sell for %s (%s) — position left open for "
                        "retry or manual redemption. YES error: %s | NO error: %s",
                        dh_id[:20], position.asset.upper(),
                        yes_sell_result.error, no_sell_result.error,
                    )
                    self.telegram.send_risk_alert(
                        "DHBothLegsFailed",
                        f"DH BOTH legs failed for {position.asset.upper()} ({dh_id[:20]}). "
                        f"YES: {yes_sell_result.error} | NO: {no_sell_result.error}",
                        severity="CRITICAL",
                    )
                else:
                    failed_leg  = "YES" if not yes_ok else "NO"
                    failed_err  = yes_sell_result.error if not yes_ok else no_sell_result.error
                    logger.warning(
                        "DH %s leg failed for %s (%s) — position left open for retry. "
                        "Error: %s",
                        failed_leg, dh_id[:20], position.asset.upper(), failed_err,
                    )
                    self.telegram.send_risk_alert(
                        "DHOneLegFailed",
                        f"DH {failed_leg} leg failed for {position.asset.upper()} "
                        f"({dh_id[:20]}) — retrying. Error: {failed_err}",
                        severity="WARNING",
                    )
                continue

            actual_proceeds: Optional[float] = None
            if not cfg.paper_mode:
                yes_proceeds = yes_sell_result.size * yes_sell_result.price if yes_sell_result.success else 0.0
                no_proceeds  = no_sell_result.size * no_sell_result.price  if no_sell_result.success  else 0.0
                actual_proceeds = yes_proceeds + no_proceeds

            yes_exit = yes_sell_result.price if yes_sell_result.success else yes_sell
            no_exit  = no_sell_result.price  if no_sell_result.success  else no_sell

            closed = self.risk_manager.register_dh_close(
                dh_id=dh_id,
                yes_exit_price=yes_exit,
                no_exit_price=no_exit,
                exit_reason=exit_reason,
                actual_proceeds_usdc=actual_proceeds,
            )

            if closed is None:
                continue

            # Release asset lock and timeout alert state
            self._asset_open_dh_position.pop(position.asset, None)
            self._timeout_alerted.discard(dh_id)
            if self.dh_detector:
                self.dh_detector.reset_cooldown(position.asset)

            pnl = closed.pnl_usdc or 0.0
            self.telegram.send_message(
                f"{'📄' if cfg.paper_mode else '✅'} *DH Closed* | {position.asset.upper()} | "
                f"PnL: ${pnl:+.2f} | {exit_reason[:60]} | "
                f"Duration: {closed.duration_seconds:.0f}s | "
                f"{'PAPER' if cfg.paper_mode else 'LIVE'}"
            )
            self.openclaw.push_dh_closed(
                dh_id=dh_id,
                asset=position.asset,
                pnl_usdc=pnl,
                exit_reason=exit_reason,
                duration_seconds=closed.duration_seconds,
                paper_mode=cfg.paper_mode,
            )

            if self.risk_manager.status == TradingStatus.KILLED:
                reason = self.risk_manager._kill_reason or "Unknown"
                self.telegram.send_kill_switch_alert(
                    reason,
                    risk_state=self.risk_manager.get_state(),
                )

            dh_risk_state = self.risk_manager.get_state()
            if dh_risk_state.circuit_breaker_active:
                resume_str = datetime.datetime.utcfromtimestamp(
                    dh_risk_state.circuit_breaker_resume_at
                ).strftime("%H:%M:%S UTC")
                self.telegram.send_risk_alert(
                    "CircuitBreaker",
                    f"Circuit breaker triggered — trading paused until {resume_str}. "
                    f"Reason: {self.risk_manager._kill_reason}",
                    severity="WARNING",
                )

    # ─────────────────────────────────────────────────────────────────────────
    # On-chain Redemption Safety Net
    # ─────────────────────────────────────────────────────────────────────────

    async def _attempt_auto_redeem(self, position: Position) -> None:
        """
        Last-resort on-chain redemption when all SELL retries are exhausted.

        Called only in LIVE mode once the market window has resolved.
        Calls redeemPositions() on Polygon to convert winning tokens → USDC,
        then syncs the on-chain balance back to the risk manager.
        """
        order_id = position.order_id
        self._auto_redeem_triggered.add(order_id)

        logger.warning(
            "AUTO-REDEEM triggered for %s | condition=%s | "
            "position age=%.0fs",
            order_id[:20], position.condition_id[:16],
            time.time() - position.opened_at,
        )

        result = await self.polymarket_client.redeem_positions(position.condition_id)

        if result["success"]:
            # Close position at neutral price — update_balance below will
            # correct the balance to the actual on-chain value.
            self.risk_manager.register_trade_close(
                order_id=order_id,
                exit_price=0.5,
            )
            self._sell_fail_count.pop(order_id, None)
            if position.asset:
                self._asset_open_position.pop(position.asset, None)
                if self.edge_detector is not None:
                    self.edge_detector.reset_cooldown(position.asset)

            # Sync actual on-chain balance to reflect redeemed USDC
            new_balance = await self.polymarket_client.get_portfolio_balance()
            if new_balance and new_balance > 0:
                self.risk_manager.update_balance(new_balance)
                logger.info(
                    "Auto-redeem balance sync: $%.2f | tx=%s",
                    new_balance, result.get("tx_hash", "")[:20],
                )

            self.telegram.send_risk_alert(
                "AutoRedeemSuccess",
                f"On-chain redeem OK for position {order_id[:12]}. "
                f"Balance synced to ${new_balance:.2f}. "
                f"Tx: {result.get('tx_hash', 'N/A')[:20]}",
                severity="WARNING",
            )
        else:
            logger.critical(
                "AUTO-REDEEM FAILED for %s — manual action required! Error: %s",
                order_id[:20], result["message"],
            )
            self.telegram.send_risk_alert(
                "AutoRedeemFailed",
                f"On-chain redeem FAILED for {order_id[:12]}. "
                f"Tokens stuck on Polymarket. Manual close required!\n"
                f"Condition: {position.condition_id}\n"
                f"Error: {result['message']}",
                severity="CRITICAL",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # OpenClaw Command Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _register_openclaw_commands(self) -> None:
        """Register handlers for commands that can be sent from the OpenClaw agent."""

        def handle_pause(cmd):
            reason = cmd.parameters.get("reason", "Paused by OpenClaw agent")
            self.risk_manager.pause(reason)
            self.telegram.send_risk_alert("TradingPaused", reason, severity="WARNING")

        def handle_resume(cmd):
            success = self.risk_manager.resume()
            if success:
                self.telegram.send_message("▶️ *Trading Resumed* by OpenClaw agent.")

        def handle_status(cmd):
            state = self.risk_manager.get_state()
            self.telegram.send_performance_summary(
                balance=state.current_balance,
                total_pnl=state.total_pnl,
                total_pnl_pct=state.total_pnl_pct * 100,
                daily_pnl=state.daily_pnl,
                win_rate=state.win_rate * 100,
                total_trades=state.total_trades,
                drawdown_pct=state.drawdown_from_peak_pct * 100,
                paper_mode=self.config.paper_mode,
            )

        def handle_reset_kill(cmd):
            confirm = cmd.parameters.get("confirm", False)
            self.risk_manager.reset_kill_switch(confirm=confirm)

        def handle_stop(cmd):
            logger.warning("STOP command received from OpenClaw agent.")
            self.stop()

        self.openclaw.register_command_handler("pause", handle_pause)
        self.openclaw.register_command_handler("resume", handle_resume)
        self.openclaw.register_command_handler("status", handle_status)
        self.openclaw.register_command_handler("reset_kill_switch", handle_reset_kill)
        self.openclaw.register_command_handler("stop", handle_stop)

    # ─────────────────────────────────────────────────────────────────────────
    # Polymarket WebSocket Callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pm_price_change(self, token_id: str, price: float, side: str, ts: float) -> None:
        """
        Callback invoked by PolymarketWSFeed on every price_change event.
        Updates the cached yes/no price for whichever active market owns this token.
        Checks both edge_detector (latency_arb) and dh_detector (dump_hedge) caches.
        """
        detectors = []
        if self.edge_detector is not None:
            detectors.append(self.edge_detector._active_market_by_asset)
        if self.dh_detector is not None:
            detectors.append(self.dh_detector._active_market_by_asset)

        for cache in detectors:
            for mkt in cache.values():
                if mkt is None:
                    continue
                if token_id == mkt.yes_token_id:
                    mkt.yes_price = price
                    return
                if token_id == mkt.no_token_id:
                    mkt.no_price = price
                    return

    def _export_trades_csv(self, date_str: str) -> None:
        """
        Dump all trades closed on *date_str* (YYYY-MM-DD, UTC) to a CSV file.

        File path:  {config.trades_csv_dir}/trades_{date_str}.csv
        Appends to an existing file so partial-day runs are additive.
        Skips trades already written (deduplicates on the 'id' column by
        reading existing rows first).
        """
        trades_dir = self.config.trades_csv_dir
        os.makedirs(trades_dir, exist_ok=True)
        filepath = os.path.join(trades_dir, f"trades_{date_str}.csv")

        FIELDS = [
            "type", "id", "asset", "strategy_detail", "market_question",
            "entry_price", "exit_price", "size_shares", "cost_usdc", "pnl_usdc",
            "locked_profit_usdc", "opened_at_utc", "closed_at_utc",
            "duration_seconds", "exit_reason", "paper_mode",
        ]

        # Collect IDs already written to avoid duplicates on repeated daily triggers.
        existing_ids: set = set()
        if os.path.exists(filepath):
            try:
                with open(filepath, newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        existing_ids.add(row.get("id", ""))
            except Exception:
                pass  # Corrupt file or missing header — will be overwritten below

        rows: list = []

        def _utc(ts: Optional[float]) -> str:
            if ts is None:
                return ""
            return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")

        # LA positions
        for pos in self.risk_manager._closed_positions:
            if pos.closed_at is None:
                continue
            closed_date = datetime.datetime.utcfromtimestamp(pos.closed_at).strftime("%Y-%m-%d")
            if closed_date != date_str:
                continue
            if pos.order_id in existing_ids:
                continue
            rows.append({
                "type": "LA",
                "id": pos.order_id,
                "asset": pos.asset,
                "strategy_detail": pos.direction,
                "market_question": pos.market_question,
                "entry_price": f"{pos.entry_price:.6f}",
                "exit_price": f"{pos.exit_price:.6f}" if pos.exit_price is not None else "",
                "size_shares": f"{pos.size_shares:.6f}",
                "cost_usdc": f"{pos.cost_usdc:.4f}",
                "pnl_usdc": f"{pos.pnl_usdc:.4f}" if pos.pnl_usdc is not None else "",
                "locked_profit_usdc": "",
                "opened_at_utc": _utc(pos.opened_at),
                "closed_at_utc": _utc(pos.closed_at),
                "duration_seconds": f"{pos.duration_seconds:.1f}",
                "exit_reason": "",
                "paper_mode": str(pos.paper_mode),
            })

        # DH positions
        for pos in self.risk_manager._closed_dh_positions:
            if pos.closed_at is None:
                continue
            closed_date = datetime.datetime.utcfromtimestamp(pos.closed_at).strftime("%Y-%m-%d")
            if closed_date != date_str:
                continue
            if pos.dh_id in existing_ids:
                continue
            combined_exit = (
                (pos.yes_exit_price or 0.0) + (pos.no_exit_price or 0.0)
                if pos.yes_exit_price is not None or pos.no_exit_price is not None
                else None
            )
            rows.append({
                "type": "DH",
                "id": pos.dh_id,
                "asset": pos.asset,
                "strategy_detail": "YES+NO",
                "market_question": pos.market_question,
                "entry_price": f"{pos.combined_entry_price:.6f}",
                "exit_price": f"{combined_exit:.6f}" if combined_exit is not None else "",
                "size_shares": f"{pos.size_shares:.6f}",
                "cost_usdc": f"{pos.combined_cost_usdc:.4f}",
                "pnl_usdc": f"{pos.pnl_usdc:.4f}" if pos.pnl_usdc is not None else "",
                "locked_profit_usdc": f"{pos.locked_profit_usdc:.4f}",
                "opened_at_utc": _utc(pos.opened_at),
                "closed_at_utc": _utc(pos.closed_at),
                "duration_seconds": f"{pos.duration_seconds:.1f}",
                "exit_reason": pos.exit_reason,
                "paper_mode": str(pos.paper_mode),
            })

        if not rows:
            return

        write_header = not os.path.exists(filepath) or os.path.getsize(filepath) == 0
        try:
            with open(filepath, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerows(rows)
            logger.info(
                "CSV export: wrote %d trade(s) to %s", len(rows), filepath
            )
        except Exception as exc:
            logger.error("CSV export failed: %s", exc)

    def _subscribe_pm_ws_to_market(self, market: MarketInfo) -> None:
        """
        Subscribe the Polymarket WS feed to a new active market.
        subscribe() is synchronous — call it directly, no create_task.
        """
        self.pm_ws_feed.subscribe(market.condition_id, market.yes_token_id, market.no_token_id)
        logger.info(
            "PM WS subscribed to market: %s (conditionId: %s)",
            market.question[:50],
            market.condition_id[:16],
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket Latency Arbitrage Bot — OpenClaw Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Run in paper mode (default)
  python main.py --live             # Run in live mode (requires credentials)
  python main.py --paper            # Explicitly run in paper mode
  python main.py --log-level DEBUG  # Enable verbose logging
        """,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--paper",
        action="store_true",
        default=False,
        help="Run in paper trading mode (default). No real orders placed.",
    )
    mode_group.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Run in live trading mode. Requires POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config.",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=None,
        help="Override starting paper balance (USDC).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = BotConfig()

    # Apply CLI overrides (default is paper mode for safety)
    if args.live:
        config.paper_mode = False
    elif args.paper:
        config.paper_mode = True

    if args.log_level:
        config.log_level = args.log_level

    if args.balance:
        config.paper_starting_balance = args.balance

    print_banner()

    # Validate configuration
    try:
        config.validate()
    except ValueError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    # Add file handler to root logger (console handlers still active until dashboard starts)
    setup_logging(config.log_file, config.log_level)

    mode_str = "PAPER MODE" if config.paper_mode else "LIVE MODE"
    logger.info("Starting Polymarket Arbitrage Bot in %s", mode_str)

    if not config.paper_mode:
        logger.warning(
            "⚠️  LIVE MODE ACTIVE — Real funds will be used. "
            "Ensure you have completed paper trading validation first."
        )

    # Initialize and run bot
    bot = PolymarketArbitrageBot(config)

    # Handle graceful shutdown on SIGINT/SIGTERM.
    # SIGINT (Ctrl+C) → set flag so the main loop shows the confirmation prompt.
    # SIGTERM (system shutdown, Docker stop) → stop immediately, no prompt.
    def sigint_handler(_sig, _frame):
        if bot._shutdown_requested:
            # Second Ctrl+C while prompt is already pending → force stop.
            logger.warning("Second SIGINT received — forcing shutdown.")
            bot.stop()
        else:
            bot._shutdown_requested = True

    def sigterm_handler(_sig, _frame):
        logger.info("SIGTERM received — shutting down.")
        bot.stop()

    signal.signal(signal.SIGINT,  sigint_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
