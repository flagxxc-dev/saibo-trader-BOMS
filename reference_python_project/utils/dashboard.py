"""
Dashboard — Rich Live, screen=True (alternate buffer).

Layout matches image/dashboard_preview.svg:

  ┌──────────────────────────────────────────────────────────────────┐
  │                      HEADER  (full width)                        │
  ├────────────────────────────────────┬─────────────────────────────┤
  │          ACTIVE MARKETS            │       OPEN POSITIONS        │
  ├───────────────┬────────────────────┴─────────────────────────────┤
  │ ENGINE STATUS │    RISK STATUS      │        RECENT LOG          │
  └───────────────┴────────────────────┴────────────────────────────┘
  ● RUNNING  PAPER MODE  POLYGON:137  STRATEGY: …  TELEGRAM: ✓  …

"""

import atexit
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from utils.logger import disable_console_logging

console  = Console(highlight=False)
_live: Optional[Live] = None
_LOG_PATH = Path(__file__).parent.parent / "polymarket_bot.log"
_NOISE    = ("httpx:", "httpcore.", "hpack.")


# ─────────────────────────────────────────────────────────────────────────────
# Live lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def _get_live() -> Live:
    global _live
    if _live is None:
        disable_console_logging()
        _live = Live(
            console=console,
            screen=True,
            refresh_per_second=4,
            transient=False,
            redirect_stdout=True,
            redirect_stderr=True,
        )
        _live.start(refresh=False)
        atexit.register(_stop_live)
    return _live


def _stop_live() -> None:
    global _live
    if _live is not None:
        try:
            _live.stop()
        except Exception:
            pass
        _live = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API — called from main.py heartbeat (every 2 s)
# ─────────────────────────────────────────────────────────────────────────────

def render_dashboard(
    feeds: dict,
    risk_state, open_positions: dict, asset_locks: dict,
    edge_stats: dict, active_markets: dict,
    edge_detector, paper_mode: bool,
    log_lines: int = 20,
    trade_window_minutes: int = 5,
    strategy: str = "latency_arb",
    open_dh_positions: Optional[dict] = None,
    dh_detector=None,
    engine_config: Optional[dict] = None,
    telegram_enabled: bool = False,
    uptime_s: float = 0.0,
) -> None:
    live = _get_live()
    live.update(_build(
        feeds, risk_state, open_positions, asset_locks,
        edge_stats, active_markets, edge_detector, paper_mode,
        log_lines, trade_window_minutes, strategy,
        open_dh_positions or {}, dh_detector, engine_config or {},
        telegram_enabled, uptime_s,
    ), refresh=True)


# ─────────────────────────────────────────────────────────────────────────────
# Full layout
# ─────────────────────────────────────────────────────────────────────────────

def _build(
    feeds, risk_state, open_positions, asset_locks,
    edge_stats, active_markets, edge_detector, paper_mode,
    log_lines, trade_window_minutes, strategy,
    open_dh_positions, dh_detector, engine_config,
    telegram_enabled, uptime_s,
):
    # Middle row: Active Markets (left 62%) | Open Positions (right 38%)
    mid = Table.grid(expand=True, padding=(0, 1))
    mid.add_column(ratio=62)
    mid.add_column(ratio=38)
    mid.add_row(
        _markets(active_markets, edge_detector, trade_window_minutes, strategy),
        _positions(open_positions, open_dh_positions, asset_locks),
    )

    # Bottom row: Engine Status | Risk Status | Recent Log  (equal thirds)
    bot = Table.grid(expand=True, padding=(0, 1))
    bot.add_column(ratio=1)
    bot.add_column(ratio=1)
    bot.add_column(ratio=1)
    bot.add_row(
        _engine_status(
            feeds, edge_detector, dh_detector,
            engine_config, strategy, paper_mode, trade_window_minutes,
        ),
        _risk_status(risk_state, engine_config),
        _logs(log_lines),
    )

    parts: list = [_header(risk_state, paper_mode, strategy, uptime_s)]

    # Binance feed cards only for strategies that actually use the feed
    if strategy != "dump_hedge" and feeds:
        parts.append(_feed_cards(feeds))

    parts += [mid, bot, _status_bar(strategy, paper_mode, telegram_enabled)]
    return Group(*parts)


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_uptime(s: float) -> str:
    s = int(max(0.0, s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _header(rs, paper_mode: bool, strategy: str, uptime_s: float = 0.0) -> Panel:
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")
    mode = Text("◆ PAPER", style="bold yellow") if paper_mode else Text("◆ LIVE", style="bold red")
    sc   = {"ACTIVE": "green", "PAUSED": "yellow", "DAILY_HALT": "orange3", "KILLED": "red"}.get(
        rs.status, "white"
    )
    bal_c = "green" if rs.current_balance >= rs.peak_balance * 0.85 else "red"
    pc    = "green" if rs.total_pnl  >= 0 else "red"
    dc    = "green" if rs.daily_pnl  >= 0 else "red"
    ps    = "+" if rs.total_pnl  >= 0 else ""
    ds    = "+" if rs.daily_pnl  >= 0 else ""
    total_open   = rs.open_positions + rs.open_dh_positions
    total_trades = rs.total_trades + rs.total_dh_trades

    row = Text.assemble(
        Text(now, style="dim"), "   ",
        mode, "  ",
        Text(f"● {rs.status}", style=f"bold {sc}"), "   ",
        Text("Uptime ", style="dim"),
        Text(_fmt_uptime(uptime_s), style="white"), "   ",
        Text("Balance ", style="dim"),
        Text(f"${rs.current_balance:.2f}", style=f"bold {bal_c}"),
        Text("   Daily ", style="dim"),
        Text(f"{ds}${rs.daily_pnl:.2f}", style=f"bold {dc}"),
        Text(f" ({ds}{rs.daily_pnl_pct:.1%})", style=f"dim {dc}"),
        Text("   Total ", style="dim"),
        Text(f"{ps}${rs.total_pnl:.2f}", style=f"bold {pc}"),
        Text(f" ({ps}{rs.total_pnl_pct:.1%})", style=f"dim {pc}"),
        Text("   Open ", style="dim"),
        Text(str(total_open), style="bold white"),
        Text("   Trades ", style="dim"),
        Text(str(total_trades), style="bold white"),
        Text(f"  ({rs.win_rate:.0%} win)", style="dim green" if rs.win_rate >= 0.6 else "dim"),
    )

    _STRATEGY_LABELS = {
        "latency_arb": "LATENCY ARB",
        "dump_hedge":  "DUMP HEDGE",
        "both":        "LATENCY ARB + DUMP HEDGE",
    }
    strat_label = _STRATEGY_LABELS.get(strategy, strategy.upper())
    return Panel(
        row,
        title=f"[bold cyan]  POLYMARKET ARB BOT  ·  {strat_label}  [/bold cyan]",
        border_style="cyan", padding=(0, 1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Price feed cards  (latency_arb / both only)
# ─────────────────────────────────────────────────────────────────────────────

_FEED_DISPLAY = {
    "btc": ("BTC", 5.0,   "cyan"),
    "eth": ("ETH", 0.53,  "blue"),
    "sol": ("SOL", 0.05,  "magenta"),
    "xrp": ("XRP", 0.01,  "green"),
}


def _feed_cards(feeds: dict) -> Columns:
    cards = []
    for asset, feed in feeds.items():
        sym, mm, color = _FEED_DISPLAY.get(asset, (asset.upper(), 1.0, "white"))
        cards.append(_feed_card(sym, feed, mm, color))
    return Columns(cards, equal=True, expand=True)


def _feed_card(sym, feed, mm, color) -> Panel:
    if feed is None or feed.latest_price is None:
        return Panel(Text("Connecting…", style="dim yellow"),
                     title=Text(sym, style=f"bold {color}"),
                     border_style=color, padding=(0, 1))

    price = feed.latest_price
    c27   = feed.get_price_change(2.7)
    c60   = feed.get_price_change(60.0)

    if c27 is None:        arrow = Text("─  FLAT", style="dim")
    elif c27 >= mm:        arrow = Text("▲  UP",   style="bold green")
    elif c27 <= -mm:       arrow = Text("▼  DOWN", style="bold red")
    else:                  arrow = Text("─  FLAT", style="dim")

    def chg(v):
        if v is None: return Text("    --", style="dim")
        s = "+" if v >= 0 else ""
        return Text(f"{s}${v:,.2f}", style="green" if v >= 0 else "red")

    ws      = Text("● OK", style="bold green") if feed.is_connected else Text("● NO", style="bold red")
    price_s = f"${price:,.2f}" if price >= 10 else f"${price:,.4f}"
    
    lag_ms = getattr(feed, "latest_lag_ms", None)
    if lag_ms is not None:
        lag_str = Text(f"{lag_ms:.0f}ms", style="dim cyan")
    else:
        lag_str = Text("--ms", style="dim")

    body = Text.assemble(
        Text(f"{price_s}\n", style="bold white"),
        Text("2.7s  ", style="dim"), chg(c27), Text("   "), arrow, Text("\n"),
        Text("60s   ", style="dim"), chg(c60), Text("\n"),
        Text("Ticks ", style="dim"), Text(f"{feed.tick_count:,}", style="white"),
        Text("   WS ", style="dim"), ws, Text(" "), lag_str,
    )
    return Panel(body, title=Text(sym, style=f"bold {color}"),
                 border_style=color, padding=(0, 1))


# ─────────────────────────────────────────────────────────────────────────────
# Active markets — DH-focused columns matching SVG
# ─────────────────────────────────────────────────────────────────────────────

_ASSET_COLOR = {
    "BTC": "yellow", "ETH": "cyan", "SOL": "magenta", "XRP": "green",
}


def _markets(
    active_markets, edge_detector, trade_window_minutes: int = 5,
    strategy: str = "latency_arb",
) -> Panel:
    window_secs = trade_window_minutes * 60
    warn_secs   = window_secs * 0.2
    crit_secs   = window_secs * 0.1

    t = Table(
        box=box.SIMPLE_HEAD, border_style="blue",
        show_lines=False, padding=(0, 1), expand=True,
    )
    t.add_column("ASSET",    style="bold white", width=6)
    t.add_column("YES BID",  justify="right",    width=9)
    t.add_column("NO BID",   justify="right",    width=9)
    t.add_column("SPREAD",   justify="right",    width=9)
    t.add_column("COMBINED", justify="right",    width=10)
    t.add_column("DISCOUNT", justify="center",   width=10)
    t.add_column("REMAIN",   justify="right",    width=8)

    for asset, mkt in active_markets.items():
        asset_upper = asset.upper()
        asset_color = _ASSET_COLOR.get(asset_upper, "white")

        if mkt is None:
            t.add_row(
                Text(asset_upper, style="dim"),
                Text("—", style="dim"), Text("—", style="dim"),
                Text("—", style="dim"), Text("—", style="dim"),
                Text("no market", style="dim"), Text("—", style="dim"),
            )
            continue

        # Seconds remaining
        if edge_detector is not None:
            secs = edge_detector._get_seconds_remaining(mkt) or 0
        else:
            from core.market_utils import get_seconds_remaining
            secs = get_seconds_remaining(mkt, float(window_secs)) or 0

        tc = "bold red" if secs < crit_secs else ("yellow" if secs < warn_secs else "green")
        time_str = f"{int(secs)//60}:{int(secs)%60:02d}" if secs >= 60 else f"{int(secs)}s"

        yes      = mkt.yes_price
        no       = mkt.no_price
        combined = yes + no
        spread   = 1.0 - combined           # absolute gap below $1.00
        roi_pct  = spread / combined if combined > 0 else 0.0  # ROI at entry

        # Combined price color: the lower, the stronger the DH signal
        if combined <= 0.95:
            comb_style = "bold bright_magenta"
        elif combined <= 0.97:
            comb_style = "bright_magenta"
        elif combined <= 0.98:
            comb_style = "green"
        elif combined <= 0.99:
            comb_style = "yellow"
        else:
            comb_style = "dim"

        # Discount (ROI) highlight
        if roi_pct >= 0.02:
            disc_style = "bold green"
        elif roi_pct >= 0.01:
            disc_style = "yellow"
        else:
            disc_style = "dim"

        t.add_row(
            Text(asset_upper,           style=f"bold {asset_color}"),
            Text(f"{yes:.4f}",          style="green"),
            Text(f"{no:.4f}",           style="red"),
            Text(f"{spread:.4f}",       style="dim"),
            Text(f"{combined:.4f}",     style=comb_style),
            Text(f"{roi_pct:.2%}",      style=disc_style),
            Text(time_str,              style=tc),
        )

    return Panel(
        t,
        title=f"[bold blue]ACTIVE MARKETS — {trade_window_minutes} MIN[/bold blue]",
        border_style="blue", padding=(0, 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Open positions — card-style for both DH and LA
# ─────────────────────────────────────────────────────────────────────────────

def _positions(open_positions: dict, open_dh_positions: dict, asset_locks: dict) -> Panel:
    cards = []

    # DH positions — purple card, matching SVG style
    for _dh_id, pos in open_dh_positions.items():
        age     = time.time() - pos.opened_at
        age_str = f"{int(age)//60:02d}:{int(age)%60:02d}"
        acolor  = _ASSET_COLOR.get(pos.asset.upper(), "white")

        body = Text()
        body.append(f"DH · {pos.asset.upper()}", style=f"bold bright_magenta")
        body.append("  [DUMP-HEDGE]\n", style="dim bright_magenta")
        body.append(f"YES  entry: {pos.yes_entry_price:.4f}\n", style="dim")
        body.append(f"NO   entry: {pos.no_entry_price:.4f}\n", style="dim")
        body.append("Locked: ", style="dim")
        body.append(f"${pos.locked_profit_usdc:.4f}", style="bold green")
        body.append("   cost: ", style="dim")
        body.append(f"${pos.combined_cost_usdc:.2f}", style="white")
        body.append(f"\nAge: {age_str}", style="dim")

        cards.append(Panel(body, border_style="bright_magenta", padding=(0, 1)))

    # LA positions — yellow card
    for oid, pos in open_positions.items():
        age       = time.time() - pos.opened_at
        age_style = "red" if age > 240 else ("yellow" if age > 120 else "white")
        dir_style = "bold green" if pos.side == "BUY" else "bold red"
        locked    = asset_locks.get(pos.asset)
        acolor    = _ASSET_COLOR.get((pos.asset or "").upper(), "white")

        body = Text()
        body.append(f"LA · {(pos.asset or '?').upper()}", style=f"bold yellow")
        body.append("  [LATENCY-ARB]\n", style="dim yellow")
        body.append("Dir:   ", style="dim")
        body.append(f"{pos.side}\n", style=dir_style)
        body.append(f"Entry: {pos.entry_price:.4f}   Cost: ${pos.cost_usdc:.2f}\n", style="dim")
        body.append("Age: ", style="dim")
        body.append(f"{age:.0f}s", style=age_style)
        body.append("   Locked: ", style="dim")
        body.append("YES" if locked else "NO", style="bold green" if locked else "dim")

        cards.append(Panel(body, border_style="yellow", padding=(0, 1)))

    if not cards:
        empty = Text.assemble(
            Text("no open positions\n", style="dim"),
        )
        return Panel(empty,
                     title="[bold white]OPEN POSITIONS[/bold white]",
                     border_style="white", padding=(0, 1))

    return Panel(
        Group(*cards),
        title="[bold white]OPEN POSITIONS[/bold white]",
        border_style="white", padding=(0, 1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Engine status
# ─────────────────────────────────────────────────────────────────────────────

def _engine_status(
    feeds, edge_detector, dh_detector, engine_config,
    strategy: str, paper_mode: bool, trade_window_minutes: int,
) -> Panel:
    cfg = engine_config or {}

    _STRATEGY_LABELS = {
        "latency_arb": "LATENCY ARB",
        "dump_hedge":  "DUMP HEDGE",
        "both":        "LA + DH",
    }
    strat_label = _STRATEGY_LABELS.get(strategy, strategy.upper())
    strat_style = "bright_magenta" if "DUMP" in strat_label else "cyan"

    mode_text = Text("PAPER", style="bold yellow") if paper_mode else Text("LIVE", style="bold red")

    # Binance feed
    if strategy in ("latency_arb", "both") and feeds:
        feed = next(iter(feeds.values()), None)
        if feed and feed.is_connected:
            binance_val = Text("● CONNECTED", style="bold green")
        elif feed:
            binance_val = Text("● RECONNECTING", style="yellow")
        else:
            binance_val = Text("● NO FEED", style="red")
    else:
        binance_val = Text("N/A", style="dim")

    dh_val = Text("● RUNNING", style="bold green") if dh_detector is not None else Text("N/A", style="dim")
    ed_val = Text("● RUNNING", style="bold green") if edge_detector is not None else Text("N/A", style="dim")

    def _cfg(key, fmt=str, suffix=""):
        v = cfg.get(key)
        if v is None:
            return Text("N/A", style="dim")
        return Text(f"{fmt(v)}{suffix}", style="white")

    rows = [
        ("Strategy",      Text(strat_label, style=strat_style)),
        ("Mode",          mode_text),
        ("Window",        Text(f"{trade_window_minutes}-min", style="white")),
        ("Binance Feed",  binance_val),
        ("DH Detector",   dh_val),
        ("Edge Detector", ed_val),
        ("Edge Threshold",
            Text(f"{edge_detector.min_edge_threshold:.0%}" if edge_detector else "N/A",
                 style="cyan" if edge_detector else "dim")),
        ("DH Sum Target", _cfg("dh_sum_target", lambda v: f"{v:.2f}")),
        ("DH Min Disc",   _cfg("dh_min_discount", lambda v: f"{v:.2f}")),
        ("Bet Size",
            Text(f"${cfg['dh_fixed_bet_usdc']:.2f} USDC" if "dh_fixed_bet_usdc" in cfg else "N/A",
                 style="white" if "dh_fixed_bet_usdc" in cfg else "dim")),
    ]

    t = Table.grid(expand=True)
    t.add_column(width=16, style="dim")
    t.add_column()
    for label, value in rows:
        t.add_row(label, value)

    return Panel(t, title="[bold white]ENGINE STATUS[/bold white]",
                 border_style="white", padding=(0, 1))


# ─────────────────────────────────────────────────────────────────────────────
# Risk status
# ─────────────────────────────────────────────────────────────────────────────

def _risk_status(rs, engine_config: dict) -> Panel:
    cfg        = engine_config or {}
    pc         = "green" if rs.total_pnl  >= 0 else "red"
    dc         = "green" if rs.daily_pnl  >= 0 else "red"
    bal_c      = "green" if rs.current_balance >= rs.peak_balance * 0.85 else "red"
    ps         = "+" if rs.total_pnl  >= 0 else ""
    ds         = "+" if rs.daily_pnl  >= 0 else ""
    draw_c     = (
        "bold red" if rs.drawdown_from_peak_pct > 0.20
        else ("yellow" if rs.drawdown_from_peak_pct > 0.10 else "green")
    )

    wins         = rs.winning_trades
    total_trades = rs.total_trades + rs.total_dh_trades
    total_open   = rs.open_positions + rs.open_dh_positions
    max_pos      = cfg.get("max_concurrent_positions", "?")

    # Daily loss limit
    dl_pct  = cfg.get("daily_loss_limit", 0.0)
    dl_usdc = rs.daily_starting_balance * dl_pct if dl_pct else 0.0
    dl_used = (-rs.daily_pnl / dl_usdc) if (dl_usdc > 0 and rs.daily_pnl < 0) else 0.0
    dl_style = "bold red" if dl_used > 0.7 else ("yellow" if dl_used > 0.4 else "dim")
    dl_str  = (f"${dl_usdc:,.0f} ({dl_used:.0%} used)" if dl_usdc else "N/A")

    # PnL attribution (LA vs DH) — only show breakdown when both strategies ran
    la_pnl = getattr(rs, "la_pnl", None)
    dh_pnl = getattr(rs, "dh_pnl", None)
    la_s = ("+" if (la_pnl or 0) >= 0 else "")
    dh_s = ("+" if (dh_pnl or 0) >= 0 else "")
    la_c = "green" if (la_pnl or 0) >= 0 else "red"
    dh_c = "green" if (dh_pnl or 0) >= 0 else "red"

    rows = [
        ("Balance",      Text(f"${rs.current_balance:.2f} USDC", style=f"bold {bal_c}")),
        ("Daily PnL",    Text(f"{ds}${rs.daily_pnl:.2f}", style=f"bold {dc}")),
        ("Total PnL",    Text(f"{ps}${rs.total_pnl:.2f}", style=f"bold {pc}")),
    ]
    if la_pnl is not None and dh_pnl is not None and (rs.total_trades > 0 or rs.total_dh_trades > 0):
        rows += [
            ("  LA PnL",  Text(f"{la_s}${la_pnl:.2f}", style=f"dim {la_c}")),
            ("  DH PnL",  Text(f"{dh_s}${dh_pnl:.2f}", style=f"dim {dh_c}")),
        ]
    rows += [
        ("Win Rate",     Text(f"{wins}/{total_trades} ({rs.win_rate:.0%})", style="white")),
    ]

    # Per-asset breakdown
    asset_stats = getattr(rs, "asset_stats", None) or {}
    _ASSET_STYLE = {"btc": "yellow", "eth": "cyan", "sol": "magenta", "xrp": "green"}
    for asset, st in asset_stats.items():
        if st["trades"] == 0:
            continue
        a_pnl = st["pnl"]
        a_sign = "+" if a_pnl >= 0 else ""
        a_col  = "green" if a_pnl >= 0 else "red"
        a_sym  = _ASSET_STYLE.get(asset.lower(), "white")
        wr_col = "green" if st["win_rate"] >= 0.55 else ("yellow" if st["win_rate"] >= 0.45 else "red")
        rows.append((
            f"  {asset.upper()}",
            Text.assemble(
                Text(f"{st['wins']}/{st['trades']} ", style="white"),
                Text(f"({st['win_rate']:.0%})", style=f"bold {wr_col}"),
                Text(f"  {a_sign}${a_pnl:.2f}", style=a_col),
            ),
        ))

    rows += [
        ("Open Pos",     Text(f"{total_open} / {max_pos}", style="white")),
        ("DH Trades",    Text(f"{rs.total_dh_trades} total", style="white")),
        ("Max Drawdown", Text(f"${rs.drawdown_from_peak:.2f} ({rs.drawdown_from_peak_pct:.1%})", style=draw_c)),
        ("Daily Limit",  Text(dl_str, style=dl_style)),
    ]

    t = Table.grid(expand=True)
    t.add_column(width=14, style="dim")
    t.add_column()
    for label, value in rows:
        t.add_row(label, value)

    return Panel(t, title="[bold white]RISK STATUS[/bold white]",
                 border_style="white", padding=(0, 1))


# ─────────────────────────────────────────────────────────────────────────────
# Log tail — clean columnar layout, timestamp on every line
# ─────────────────────────────────────────────────────────────────────────────

# Matches: "2024-01-15 10:23:45 [INFO    ] core.module: message"
_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[(\w+)\s*\] ([\w.]+): (.+)$"
)

_LEVEL_STYLE = {
    "CRITICAL": ("CRIT", "bold red"),
    "ERROR":    ("ERR ", "red"),
    "WARNING":  ("WARN", "yellow"),
    "INFO":     ("INFO", "cyan"),
    "DEBUG":    ("DEBG", "dim"),
}


def _logs(n: int = 20) -> Panel:
    lines = _tail(n)
    text  = Text()
    for i, raw in enumerate(lines):
        if i > 0:
            text.append("\n")
        m = _LOG_RE.match(raw)
        if not m:
            text.append(f"  {raw}", style="dim")
            continue

        _date, hms, level, module, message = m.groups()
        short_level, level_style = _LEVEL_STYLE.get(level, (level[:4], "white"))
        short_module = module.split(".")[-1]

        text.append(f"{hms} ", style="dim")
        text.append(f"{short_level} ", style=f"bold {level_style}")
        text.append(f"{short_module:<18} ", style="dim")
        text.append(message, style=level_style)

    return Panel(
        text,
        title=Text.assemble(
            Text("RECENT LOG ", style="bold white"),
            Text(f"· last {n} · {_LOG_PATH.name}", style="dim"),
        ),
        border_style="white", padding=(0, 1),
    )


def _tail(n: int) -> list:
    if not _LOG_PATH.exists():
        return [f"(log file not found: {_LOG_PATH.name})"]
    try:
        with _LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        filtered = [ln.rstrip() for ln in lines
                    if ln.strip() and not any(p in ln for p in _NOISE)]
        return filtered[-n:] if filtered else ["(no log entries yet)"]
    except Exception as exc:
        return [f"(error reading log: {exc})"]


# ─────────────────────────────────────────────────────────────────────────────
# Status bar  (full-width line at the bottom)
# ─────────────────────────────────────────────────────────────────────────────

def _status_bar(strategy: str, paper_mode: bool, telegram_enabled: bool) -> Text:
    tg_text = Text("TELEGRAM: ✓", style="green") if telegram_enabled else Text("TELEGRAM: ✗", style="dim")
    mode_str = "PAPER MODE" if paper_mode else "LIVE MODE"
    mode_style = "bold yellow" if paper_mode else "bold red"
    strat_style = "bright_magenta" if "dump" in strategy else "cyan"

    bar = Text.assemble(
        Text(" ● RUNNING ", style="bold green"),
        Text("│ ", style="dim"),
        Text(mode_str, style=mode_style),
        Text("  │  POLYGON:137  │  ", style="dim"),
        Text(f"STRATEGY: {strategy}", style=strat_style),
        Text("  │  ", style="dim"),
        tg_text,
        Text(f"  │  LOG: {_LOG_PATH.name}  │  ", style="dim"),
        Text("Ctrl+C to stop", style="dim"),
    )
    return bar
