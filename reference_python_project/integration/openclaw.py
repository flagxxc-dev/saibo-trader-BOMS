"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  OPENCLAW AGENT INTEGRATION                                                  ║
║                                                                              ║
║  Connects the Polymarket bot to the user's OpenClaw AI agent.               ║
║  Enables the agent to:                                                       ║
║    • Monitor bot performance in real time                                    ║
║    • Receive periodic trade summaries and risk state reports                ║
║    • Send commands (pause, resume, adjust parameters)                        ║
║    • Trigger alerts when anomalies are detected                              ║
║                                                                              ║
║  OpenClaw is an AI agent platform that runs on local devices and            ║
║  connects to messaging platforms. This integration uses the OpenClaw        ║
║  HTTP API to push events and pull commands.                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import requests

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AgentEvent:
    """An event to push to the OpenClaw agent."""
    event_type: str          # e.g., "trade_opened", "trade_closed", "risk_alert"
    payload: Dict[str, Any]
    timestamp: float = 0.0
    bot_id: str = "polymarket_arb_bot"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp or time.time()
        return d


@dataclass
class AgentCommand:
    """A command received from the OpenClaw agent."""
    command: str             # e.g., "pause", "resume", "set_parameter", "status"
    parameters: Dict[str, Any]
    issued_at: float
    command_id: str


class OpenClawIntegration:
    """
    Bidirectional integration with the OpenClaw AI agent platform.

    Push model: The bot pushes events (trade signals, executions, risk alerts,
    performance summaries) to the OpenClaw agent at configurable intervals.

    Pull model: The bot polls the OpenClaw agent for pending commands and
    executes them (pause/resume trading, adjust parameters, request status).

    This integration is designed to be non-blocking — all HTTP calls are
    wrapped in try/except so a network failure never halts the trading loop.
    """

    # OpenClaw API endpoints
    EVENTS_ENDPOINT = "/v1/agents/{agent_id}/events"
    COMMANDS_ENDPOINT = "/v1/agents/{agent_id}/commands/pending"
    COMMAND_ACK_ENDPOINT = "/v1/agents/{agent_id}/commands/{command_id}/ack"
    MEMORY_ENDPOINT = "/v1/agents/{agent_id}/memory"

    def __init__(
        self,
        api_key: str,
        api_url: str,
        agent_id: str,
        report_interval_seconds: int = 300,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            api_key: OpenClaw API authentication token.
            api_url: Base URL for the OpenClaw API.
            agent_id: The ID of the OpenClaw agent to communicate with.
            report_interval_seconds: How often to push a performance summary.
            enabled: If False, all operations are no-ops (useful for testing).
        """
        self.api_key = api_key
        # Strip URL fragment (#token=...) — fragments are browser-only and are
        # never sent in HTTP requests, so they must be removed before use.
        parsed = urlparse(api_url)
        if parsed.fragment:
            logger.warning(
                "OPENCLAW_API_URL contains a URL fragment (#%s...) which is not "
                "sent in HTTP requests. Stripping it automatically. "
                "Set OPENCLAW_API_URL to the bare API base URL (e.g. http://localhost:18789).",
                parsed.fragment[:20],
            )
        clean_url = urlunparse(parsed._replace(fragment="", query=""))
        self.api_url = clean_url.rstrip("/")
        self.agent_id = agent_id
        self.report_interval_seconds = report_interval_seconds
        self.enabled = enabled and bool(api_key) and bool(agent_id)

        self._last_report_time: float = 0.0
        self._events_sent: int = 0
        self._commands_received: int = 0
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PolymarketArbitrageBot/1.0",
        })

        # Command handlers: map command names to callable handlers
        self._command_handlers: Dict[str, Callable] = {}

        # Thread pool for fire-and-forget HTTP calls (avoids blocking asyncio loop)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="openclaw")

        if self.enabled:
            logger.info(
                "OpenClaw integration enabled | Agent: %s | Report interval: %ds",
                agent_id,
                report_interval_seconds,
            )
        else:
            logger.info(
                "OpenClaw integration DISABLED "
                "(no API key/agent ID configured, or explicitly disabled)."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Command Handler Registration
    # ─────────────────────────────────────────────────────────────────────────

    def register_command_handler(self, command: str, handler: Callable) -> None:
        """
        Register a handler function for a specific command name.

        The handler will be called with the AgentCommand object when
        the command is received from the OpenClaw agent.

        Example:
            integration.register_command_handler("pause", lambda cmd: risk_manager.pause())
        """
        self._command_handlers[command] = handler
        logger.debug("Registered command handler for: %s", command)

    # ─────────────────────────────────────────────────────────────────────────
    # Event Pushing
    # ─────────────────────────────────────────────────────────────────────────

    def push_trade_opened(
        self,
        order_id: str,
        market_question: str,
        side: str,
        price: float,
        size_usdc: float,
        edge: float,
        paper_mode: bool,
    ) -> None:
        """Notify the OpenClaw agent that a new trade was opened."""
        self._push_event(AgentEvent(
            event_type="trade_opened",
            payload={
                "order_id": order_id,
                "market": market_question[:100],
                "side": side,
                "price": round(price, 4),
                "size_usdc": round(size_usdc, 2),
                "edge": round(edge, 4),
                "paper_mode": paper_mode,
            },
        ))

    def push_trade_closed(
        self,
        order_id: str,
        pnl_usdc: float,
        exit_price: float,
        duration_seconds: float,
        paper_mode: bool,
    ) -> None:
        """Notify the OpenClaw agent that a position was closed."""
        self._push_event(AgentEvent(
            event_type="trade_closed",
            payload={
                "order_id": order_id,
                "pnl_usdc": round(pnl_usdc, 2),
                "exit_price": round(exit_price, 4),
                "duration_seconds": round(duration_seconds, 1),
                "paper_mode": paper_mode,
                "result": "WIN" if pnl_usdc > 0 else "LOSS",
            },
        ))

    def push_risk_alert(self, alert_type: str, message: str, severity: str = "WARNING") -> None:
        """Push a risk management alert to the OpenClaw agent."""
        self._push_event(AgentEvent(
            event_type="risk_alert",
            payload={
                "alert_type": alert_type,
                "message": message,
                "severity": severity,
            },
        ))

    def push_performance_summary(self, risk_state: Any, edge_stats: dict, feed_stats: dict) -> None:
        """
        Push a comprehensive performance summary to the OpenClaw agent.
        Called periodically based on report_interval_seconds.
        """
        now = time.time()
        if now - self._last_report_time < self.report_interval_seconds:
            return

        self._last_report_time = now
        self._push_event(AgentEvent(
            event_type="performance_summary",
            payload={
                "risk": {
                    "status": risk_state.status,
                    "balance": round(risk_state.current_balance, 2),
                    "total_pnl": round(risk_state.total_pnl, 2),
                    "total_pnl_pct": round(risk_state.total_pnl_pct * 100, 2),
                    "daily_pnl": round(risk_state.daily_pnl, 2),
                    "daily_pnl_pct": round(risk_state.daily_pnl_pct * 100, 2),
                    "drawdown_pct": round(risk_state.drawdown_from_peak_pct * 100, 2),
                    "win_rate": round(risk_state.win_rate * 100, 1),
                    "total_trades": risk_state.total_trades,
                    "open_positions": risk_state.open_positions,
                    "open_dh_positions": getattr(risk_state, "open_dh_positions", 0),
                    "total_dh_trades": getattr(risk_state, "total_dh_trades", 0),
                },
                "strategy_stats": edge_stats,
                "binance_feed": feed_stats,
            },
        ))

    def push_dh_opened(
        self,
        dh_id: str,
        asset: str,
        yes_price: float,
        no_price: float,
        combined_price: float,
        locked_profit_usdc: float,
        size_shares: float,
        combined_cost_usdc: float,
        paper_mode: bool,
    ) -> None:
        """Notify the OpenClaw agent that a dump-hedge position was opened."""
        self._push_event(AgentEvent(
            event_type="dh_opened",
            payload={
                "dh_id": dh_id,
                "asset": asset.upper(),
                "yes_price": round(yes_price, 4),
                "no_price": round(no_price, 4),
                "combined_price": round(combined_price, 4),
                "discount_pct": round((1.0 - combined_price) * 100, 2),
                "locked_profit_usdc": round(locked_profit_usdc, 4),
                "size_shares": round(size_shares, 4),
                "combined_cost_usdc": round(combined_cost_usdc, 2),
                "paper_mode": paper_mode,
            },
        ))

    def push_dh_closed(
        self,
        dh_id: str,
        asset: str,
        pnl_usdc: float,
        exit_reason: str,
        duration_seconds: float,
        paper_mode: bool,
    ) -> None:
        """Notify the OpenClaw agent that a dump-hedge position was closed."""
        self._push_event(AgentEvent(
            event_type="dh_closed",
            payload={
                "dh_id": dh_id,
                "asset": asset.upper(),
                "pnl_usdc": round(pnl_usdc, 4),
                "exit_reason": exit_reason[:120],
                "duration_seconds": round(duration_seconds, 1),
                "paper_mode": paper_mode,
                "result": "WIN" if pnl_usdc > 0 else ("LOSS" if pnl_usdc < 0 else "BREAKEVEN"),
            },
        ))

    def push_kill_switch_alert(self, reason: str) -> None:
        """Push a critical kill switch alert to the OpenClaw agent."""
        self._push_event(AgentEvent(
            event_type="kill_switch_triggered",
            payload={
                "reason": reason,
                "severity": "CRITICAL",
                "action_required": "Manual investigation and reset required.",
            },
        ))

    def push_startup_notification(self, config_summary: dict) -> None:
        """Notify the OpenClaw agent that the bot has started."""
        strategy = config_summary.get("strategy", "unknown")
        mode = "PAPER" if config_summary.get("paper_mode") else "LIVE"
        self._push_event(AgentEvent(
            event_type="bot_started",
            payload={
                "config": config_summary,
                "message": (
                    f"OpenClaw Polymarket Arb Bot started — "
                    f"strategy={strategy.upper()} mode={mode}"
                ),
            },
        ))

    # ─────────────────────────────────────────────────────────────────────────
    # Command Polling
    # ─────────────────────────────────────────────────────────────────────────

    async def poll_and_execute_commands(self) -> int:
        """
        Poll the OpenClaw agent for pending commands and execute them.

        Runs the blocking HTTP poll in a thread pool so the asyncio event
        loop is never stalled waiting for the OpenClaw API.

        Returns:
            Number of commands processed.
        """
        if not self.enabled:
            return 0

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_poll_and_execute)

    def _sync_poll_and_execute(self) -> int:
        """Synchronous worker executed in a thread pool by poll_and_execute_commands."""
        commands = self._fetch_pending_commands()
        for cmd in commands:
            self._execute_command(cmd)
        return len(commands)

    def _fetch_pending_commands(self) -> List[AgentCommand]:
        """Fetch pending commands from the OpenClaw API."""
        try:
            url = self.api_url + self.COMMANDS_ENDPOINT.format(agent_id=self.agent_id)
            response = self._session.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            commands = []
            for item in data.get("commands", []):
                commands.append(AgentCommand(
                    command=item.get("command", ""),
                    parameters=item.get("parameters", {}),
                    issued_at=item.get("issued_at", time.time()),
                    command_id=item.get("id", ""),
                ))
            return commands
        except requests.exceptions.RequestException as exc:
            logger.debug("OpenClaw command poll failed (non-critical): %s", exc)
            return []

    def _execute_command(self, cmd: AgentCommand) -> None:
        """Execute a command received from the OpenClaw agent."""
        self._commands_received += 1
        logger.info(
            "OpenClaw command received: %s | params: %s",
            cmd.command,
            cmd.parameters,
        )

        handler = self._command_handlers.get(cmd.command)
        if handler:
            try:
                handler(cmd)
                self._acknowledge_command(cmd.command_id, success=True)
            except Exception as exc:
                logger.error("Command handler for '%s' failed: %s", cmd.command, exc)
                self._acknowledge_command(cmd.command_id, success=False, error=str(exc))
        else:
            logger.warning("No handler registered for command: %s", cmd.command)
            self._acknowledge_command(
                cmd.command_id,
                success=False,
                error=f"Unknown command: {cmd.command}",
            )

    def _acknowledge_command(
        self, command_id: str, success: bool, error: Optional[str] = None
    ) -> None:
        """Acknowledge command execution back to the OpenClaw agent."""
        if not command_id:
            return
        try:
            url = self.api_url + self.COMMAND_ACK_ENDPOINT.format(
                agent_id=self.agent_id,
                command_id=command_id,
            )
            self._session.post(
                url,
                json={"success": success, "error": error},
                timeout=5,
            )
        except requests.exceptions.RequestException:
            pass  # Non-critical

    # ─────────────────────────────────────────────────────────────────────────
    # Memory / Context Sync
    # ─────────────────────────────────────────────────────────────────────────

    def sync_memory(self, key: str, value: Any) -> None:
        """
        Store a key-value pair in the OpenClaw agent's persistent memory.
        Useful for persisting bot state across restarts.
        """
        if not self.enabled:
            return
        try:
            url = self.api_url + self.MEMORY_ENDPOINT.format(agent_id=self.agent_id)
            self._session.post(
                url,
                json={"key": key, "value": value},
                timeout=5,
            )
        except requests.exceptions.RequestException as exc:
            logger.debug("OpenClaw memory sync failed (non-critical): %s", exc)

    def read_memory(self, key: str) -> Optional[Any]:
        """Read a value from the OpenClaw agent's persistent memory."""
        if not self.enabled:
            return None
        try:
            url = self.api_url + self.MEMORY_ENDPOINT.format(agent_id=self.agent_id)
            response = self._session.get(url, params={"key": key}, timeout=5)
            response.raise_for_status()
            return response.json().get("value")
        except requests.exceptions.RequestException:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _push_event(self, event: AgentEvent) -> None:
        """Push an event to the OpenClaw agent API (non-blocking, fire-and-forget).

        Submits the HTTP call to a thread pool so the asyncio event loop is
        never stalled, even if the OpenClaw API is slow or unreachable.
        """
        if not self.enabled:
            logger.debug(
                "OpenClaw disabled — event not sent: %s", event.event_type
            )
            return

        self._executor.submit(self._http_post_event, event)

    def _http_post_event(self, event: AgentEvent) -> None:
        """Synchronous HTTP POST executed in the thread pool by _push_event."""
        try:
            url = self.api_url + self.EVENTS_ENDPOINT.format(agent_id=self.agent_id)
            payload = event.to_dict()
            response = self._session.post(url, json=payload, timeout=5)
            response.raise_for_status()
            self._events_sent += 1
            logger.debug(
                "OpenClaw event sent: %s (total: %d)",
                event.event_type,
                self._events_sent,
            )
        except requests.exceptions.RequestException as exc:
            # Non-critical: log at debug level to avoid spamming logs
            logger.debug(
                "OpenClaw event push failed (non-critical): %s | event: %s",
                exc,
                event.event_type,
            )
