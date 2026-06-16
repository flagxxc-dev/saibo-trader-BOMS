import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone

from rich.live import Live
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich import box

console = Console()

# Track per-tick-rate window
_prev_tick_counts = {"btc": 0, "eth": 0, "sol": 0}
_tick_rates = {"btc": 0.0, "eth": 0.0, "sol": 0.0}
_last_rate_ts = time.time()

PAPER_START = 1000.0
STATUS_LABELS = {0: "ACTIVE", 1: "DAILY HALT", 2: "KILLED", 3: "PAUSED"}
STATUS_COLORS = {0: "bold green", 1: "bold orange3", 2: "bold red", 3: "bold yellow"}


class Dashboard:
    def __init__(self):
        self.data: dict = {}
        self.start_time = time.time()
        self.combined_log: deque = deque(maxlen=60)  # merged telemetry + signals
        self._last_tlog: list = []
        self._last_slog: list = []

    # ------------------------------------------------------------------ #
    # Data update                                                          #
    # ------------------------------------------------------------------ #
    def update(self, data: dict):
        global _prev_tick_counts, _tick_rates, _last_rate_ts
        self.data = data

        # Compute tick rates (~1s window)
        now = time.time()
        elapsed = now - _last_rate_ts
        if elapsed >= 1.0:
            for sym in ("btc", "eth", "sol"):
                key = f"{sym}TickRate"
                cur = data.get(key, 0)
                _tick_rates[sym] = (cur - _prev_tick_counts[sym]) / elapsed
                _prev_tick_counts[sym] = cur
            _last_rate_ts = now

        # Merge new log lines into combined_log
        tlog = data.get("telemetryLog", [])
        slog = data.get("signalLog", [])
        for line in tlog:
            if line not in self._last_tlog:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self.combined_log.appendleft(f"[dim]{ts}[/dim] [cyan]{line}[/cyan]")
        for line in slog:
            if line not in self._last_slog:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self.combined_log.appendleft(f"[dim]{ts}[/dim] [yellow]{line}[/yellow]")
        self._last_tlog = list(tlog)
        self._last_slog = list(slog)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #
    def _uptime(self) -> str:
        s = int(time.time() - self.start_time)
        h, r = divmod(s, 3600); m, sec = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def _pnl_text(self, val: float, prefix: str = "") -> Text:
        color = "bold green" if val >= 0 else "bold red"
        sign = "+" if val >= 0 else ""
        return Text(f"{prefix}{sign}${val:.2f}", style=color)

    def _pct_text(self, val: float) -> Text:
        color = "green" if val >= 0 else "red"
        sign = "+" if val >= 0 else ""
        return Text(f"({sign}{val:.2f}%)", style=f"dim {color}")

    # ------------------------------------------------------------------ #
    # Header                                                               #
    # ------------------------------------------------------------------ #
    def _header(self) -> Panel:
        d = self.data
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")
        status = d.get("status", 0)
        sl = STATUS_LABELS.get(status, "UNKNOWN")
        sc = STATUS_COLORS.get(status, "bold white")

        balance   = d.get("balance", 0.0)
        daily_pnl = d.get("dailyPnl", 0.0)
        total_pnl = d.get("totalPnl", 0.0)
        daily_s   = d.get("dailyStartingBalance", 1.0)
        peak      = d.get("peakBalance", balance) or balance
        is_paper  = d.get("isPaperMode", True)
        start_bal = d.get("startingBalance", 1000.0)

        dpct = (daily_pnl / daily_s  * 100) if daily_s  > 0 else 0.0
        tpct = (total_pnl / start_bal * 100) if start_bal > 0 else 0.0
        tot_trades = d.get("totalTrades", 0) + d.get("totalDhTrades", 0)
        wr = d.get("winRate", 0.0)

        row = Text.assemble(
            (now_str, "dim"), "   ",
            ("◆ PAPER" if is_paper else "◆ LIVE", "bold yellow" if is_paper else "bold green"), "  ",
            (f"● {sl}", sc), "   ",
            ("Uptime ", "dim"), (self._uptime(), "white"), "   ",
            ("Balance ", "dim"), (f"${balance:,.2f}", "bold green"), "   ",
            ("Daily ", "dim"),
        )
        row.append_text(self._pnl_text(daily_pnl))
        row.append(" ")
        row.append_text(self._pct_text(dpct))
        row.append("   ")
        row.append("Total ", style="dim")
        row.append_text(self._pnl_text(total_pnl))
        row.append(" ")
        row.append_text(self._pct_text(tpct))
        row.append(f"   Open {d.get('openCount', 0)}  ", style="dim")
        row.append(f"Trades {tot_trades}", style="white")
        row.append(f"  ({wr:.1f}% win)", style="dim green")

        return Panel(row,
                     title="[bold cyan]  POLYMARKET ARB BOT  ·  LATENCY ARB + DUMP HEDGE  [/bold cyan]",
                     border_style="cyan", padding=(0, 1))

    # ------------------------------------------------------------------ #
    # Feed cards with tick rate / latency                                  #
    # ------------------------------------------------------------------ #
    def _feed_cards(self) -> Columns:
        d = self.data

        def card(sym: str, color: str) -> Panel:
            sd = d.get(f"{sym.lower()}Data", {})
            price = sd.get("price", d.get(f"{sym.lower()}Price", 0.0))
            c27   = sd.get("delta27", 0.0)
            c60   = sd.get("delta60", 0.0)
            ticks = sd.get("count", 0)
            rate  = _tick_rates.get(sym.lower(), 0.0)

            def chg(v: float) -> Text:
                s = "+" if v >= 0 else ""
                col = "green" if v >= 0 else "red"
                return Text(f"{s}${v:,.2f}", style=col)

            arrow = (Text("▲ UP",   style="bold green") if c27 > 0.05
                     else Text("▼ DOWN", style="bold red") if c27 < -0.05
                     else Text("─ FLAT", style="dim"))

            if price > 0:
                body = Text.assemble(
                    (f"${price:,.2f}\n", "bold white"),
                    ("2.7s  ", "dim"), chg(c27), "   ", arrow, "\n",
                    ("60s   ", "dim"), chg(c60), "\n",
                    ("Ticks ", "dim"), (f"{ticks:,}", "white"), "   ",
                    ("Rate  ", "dim"), (f"{rate:.1f}/s", "cyan"), "   ",
                    ("WS ", "dim"), ("● OK", "bold green"),
                )
            else:
                body = Align.center(Text("connecting…", style="dim yellow"), vertical="middle")

            return Panel(body, title=f"[bold {color}]{sym}[/bold {color}]",
                         border_style=color, padding=(0, 1))

        return Columns([card("BTC", "yellow"), card("ETH", "cyan"), card("SOL", "magenta")],
                       equal=True, expand=True)

    # ------------------------------------------------------------------ #
    # Open Positions (with Strategy column)                                #
    # ------------------------------------------------------------------ #
    def _positions(self) -> Panel:
        t = Table(box=box.SIMPLE_HEAD, border_style="white", expand=True, padding=(0, 1))
        t.add_column("STRAT",  style="bold cyan",    width=6)
        t.add_column("ASSET",  style="bold white",   width=6)
        t.add_column("SIDE",                          width=5)
        t.add_column("ENTRY",  justify="right",       width=8)
        t.add_column("SIZE",   justify="right",       width=8)
        t.add_column("COST $", justify="right",       width=8)
        t.add_column("UNRL P&L", justify="right",    width=10)

        positions = self.data.get("openPositions", [])
        if not positions:
            return Panel(
                Align.center(Text("no open positions", style="dim"), vertical="middle"),
                title="[bold white]OPEN POSITIONS[/bold white]",
                border_style="white", padding=(0, 1))

        for p in positions:
            pnl  = p.get("pnl", 0.0)
            cost = p.get("cost", 0.0)
            strat = p.get("strategy", "LA")
            strat_color = "magenta" if strat == "DH" else "cyan"
            t.add_row(
                Text(strat, style=f"bold {strat_color}"),
                p.get("asset", "?").upper(),
                p.get("side", "BUY"),
                f"{p.get('entryPrice', 0):.4f}",
                f"{p.get('size', 0):.2f}",
                f"${cost:.2f}",
                Text(f"{'+' if pnl >= 0 else ''}${pnl:.2f}", style="bold green" if pnl >= 0 else "bold red"),
            )
        return Panel(t, title="[bold white]OPEN POSITIONS[/bold white]",
                     border_style="white", padding=(0, 0))

    # ------------------------------------------------------------------ #
    # Market Discounts                                                     #
    # ------------------------------------------------------------------ #
    def _markets(self) -> Panel:
        t = Table(box=box.SIMPLE_HEAD, border_style="white", expand=True)
        t.add_column("MARKET",  style="bold white")
        t.add_column("YES",  justify="right")
        t.add_column("NO",   justify="right")
        t.add_column("COMB", justify="right", style="dim")
        t.add_column("DISC", justify="right", style="bold green")

        opps = self.data.get("dhOpportunities", [])
        if not opps:
            return Panel(
                Align.center(Text("no active markets", style="dim"), vertical="middle"),
                title="[bold white]MARKET DISCOUNTS[/bold white]", border_style="white")

        for o in opps:
            disc = o.get("discountPct", 0.0)
            disc_col = "bold green" if disc > 0 else "dim"
            t.add_row(
                o.get("question", "")[:38] + "…",
                f"{o.get('yesPrice', 0):.4f}",
                f"{o.get('noPrice',  0):.4f}",
                f"{o.get('combined', 0):.4f}",
                Text(f"{disc:.2f}%", style=disc_col),
            )
        return Panel(t, title="[bold white]MARKET DISCOUNTS[/bold white]", border_style="white")

    # ------------------------------------------------------------------ #
    # Engine Status                                                        #
    # ------------------------------------------------------------------ #
    def _engine_status(self) -> Panel:
        d = self.data
        la_trades = d.get("totalTrades", 0)
        dh_trades = d.get("totalDhTrades", 0)
        lih_trades = d.get("totalLihTrades", 0)
        lih_on = d.get("lihEnabled", True)
        strat_label = "LIH primary" if lih_on else "DH legacy"

        t = Table.grid(expand=True)
        t.add_column(width=16, style="dim")
        t.add_column()
        t.add_row("Strategy",      Text(strat_label, style="bold green"))
        if lih_on:
            t.add_row("LIH Trades",    Text(str(lih_trades), style="cyan"))
        else:
            t.add_row("DH Trades",     Text(str(dh_trades), style="magenta"))
        if la_trades:
            t.add_row("Legacy LA", Text(str(la_trades), style="dim"))
        t.add_row("Execution",     "Event-driven C++")
        t.add_row("Heartbeat",     Text("OK", style="bold green"))
        return Panel(t, title="[bold white]ENGINE STATUS[/bold white]",
                     border_style="white", padding=(0, 1))

    # ------------------------------------------------------------------ #
    # Risk Status (precise numbers + per-strategy PnL)                    #
    # ------------------------------------------------------------------ #
    def _risk_status(self) -> Panel:
        d = self.data
        balance   = d.get("balance",   PAPER_START)
        daily_pnl = d.get("dailyPnl",  0.0)
        total_pnl = d.get("totalPnl",  0.0)
        dh_pnl    = d.get("dhPnl",     0.0)
        lih_pnl   = d.get("lihPnl",    0.0)
        lih_on    = d.get("lihEnabled", True)
        open_pos  = d.get("openCount", 0)
        drawdown  = d.get("maxDrawdownPct", 0.0)
        is_paper  = d.get("isPaperMode", True)
        start_bal = d.get("startingBalance", 1000.0)
        daily_limit = start_bal * 0.20  # 20% daily limit

        daily_used_pct = abs(daily_pnl) / daily_limit * 100 if daily_limit > 0 else 0.0

        t = Table.grid(expand=True)
        t.add_column(width=16, style="dim")
        t.add_column()
        t.add_row("Balance",   Text(f"${balance:,.2f} USDC", style="bold green"))
        t.add_row("Daily PnL", self._pnl_text(daily_pnl))
        t.add_row("Total PnL", self._pnl_text(total_pnl))
        if lih_on:
            t.add_row("LIH PnL",   Text(f"{'+' if lih_pnl>=0 else ''}${lih_pnl:.2f}", style="cyan"))
        else:
            t.add_row("DH PnL",    Text(f"{'+' if dh_pnl>=0 else ''}${dh_pnl:.2f}", style="magenta"))
        closed = d.get("totalLihTrades", 0) if lih_on else d.get("totalDhTrades", 0)
        wr        = d.get("winRate", 0.0)

        t.add_row("Win Rate",  Text(f"{wr:.1f}%  ({closed} closed)", style="white"))
        t.add_row("Open Pos",  f"{open_pos} / 3")
        t.add_row("Max Drawdown", Text(f"${abs(total_pnl - 0):.2f} ({drawdown:.2f}%)",
                                       style="red" if drawdown > 5 else "white"))
        t.add_row("Daily Limit", f"${daily_limit:.0f} ({daily_used_pct:.1f}% used)")
        return Panel(t, title="[bold white]RISK STATUS[/bold white]",
                     border_style="white", padding=(0, 1))

    # ------------------------------------------------------------------ #
    # Telemetry Log (live trades + signals)                                #
    # ------------------------------------------------------------------ #
    def _logs(self) -> Panel:
        lines = list(self.combined_log)[:18]  # show latest 18 lines

        if not lines:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            lines = [f"[dim]{ts}[/dim] [dim]Waiting for signals…[/dim]"]

        text = Text()
        for line in lines:
            text.append_text(Text.from_markup(line + "\n"))

        return Panel(text, title="[bold white]TELEMETRY LOG  [dim](cyan=trades  yellow=signals)[/dim][/bold white]",
                     border_style="white", padding=(0, 1))

    # ------------------------------------------------------------------ #
    # Layout assembly                                                      #
    # ------------------------------------------------------------------ #
    def build(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header",  size=3),
            Layout(name="feeds",   size=8),
            Layout(name="mid",     ratio=1),
            Layout(name="bot",     size=17),
            Layout(name="footer",  size=1),
        )

        layout["header"].update(self._header())
        layout["feeds"].update(self._feed_cards())

        mid = Layout()
        mid.split_row(Layout(name="markets", ratio=58), Layout(name="positions", ratio=42))
        mid["markets"].update(self._markets())
        mid["positions"].update(self._positions())
        layout["mid"].update(mid)

        bot = Layout()
        bot.split_row(Layout(name="engine", ratio=1),
                      Layout(name="risk",   ratio=1),
                      Layout(name="logs",   ratio=2))
        bot["engine"].update(self._engine_status())
        bot["risk"].update(self._risk_status())
        bot["logs"].update(self._logs())
        layout["bot"].update(bot)

        d = self.data
        status = d.get("status", 0)
        sl = STATUS_LABELS.get(status, "UNKNOWN")
        sc = STATUS_COLORS.get(status, "bold white")
        is_paper  = d.get("isPaperMode", True)
        start_bal = d.get("startingBalance", 1000.0)
        strat_footer = "STRATEGY: LIH" if d.get("lihEnabled", True) else "STRATEGY: DH (legacy)"
        
        footer = Text.assemble(
            (f" ● {sl} ", sc), "│ ",
            ("PAPER MODE" if is_paper else "LIVE TRADING", "bold yellow" if is_paper else "bold green"),
            (f" · ${start_bal:,.0f} BASE", "white"), " │ POLYGON:137 │ ",
            (strat_footer, "cyan"), " │ Ctrl+C to stop",
        )
        layout["footer"].update(footer)
        return layout


# ------------------------------------------------------------------ #
# Async main — reads from stdout pipe via bridge                      #
# ------------------------------------------------------------------ #
async def run_dashboard():
    dash = Dashboard()
    uri = "ws://127.0.0.1:8080"

    import websockets

    # Pass the dashboard build method as a callable to Live for automatic refreshing
    with Live(dash.build(), refresh_per_second=4, screen=True, console=console) as live:
        while True:
            try:
                async with websockets.connect(uri, ping_interval=None) as ws:
                    while True:
                        raw = await ws.recv()
                        try:
                            # 1. Update internal data
                            dash.update(json.loads(raw))
                            # 2. Update the live display with the new layout
                            live.update(dash.build())
                        except json.JSONDecodeError:
                            pass
            except Exception:
                # On connection error, just wait and retry
                await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(run_dashboard())
    except KeyboardInterrupt:
        console.print("\n[bold red]Dashboard stopped.[/bold red]")
