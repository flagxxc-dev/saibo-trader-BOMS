"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  POLYMARKET — by Genoshide | polymarket arbitrage script bot                ║
║  healthcheck.py — pre-flight system checker                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Run before starting the bot to verify:
  - Python version compatibility
  - All required packages are installed
  - .env file exists and required variables are set
  - Polymarket CLOB API is reachable
  - Binance WebSocket endpoint is reachable
  - Telegram bot token is valid (optional)
  - OpenClaw API is reachable (optional)

Usage:
  python healthcheck.py
  make health
"""

import asyncio
import importlib
import os
import platform
import socket
import sys
import time
from typing import Callable, List, Tuple

# ─── Attempt rich import for pretty output ────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    _HAS_RICH = True
    console = Console()
except ImportError:
    _HAS_RICH = False
    console = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "[bold green]  PASS[/bold green]" if _HAS_RICH else "  PASS"
FAIL = "[bold red]  FAIL[/bold red]" if _HAS_RICH else "  FAIL"
WARN = "[bold yellow]  WARN[/bold yellow]" if _HAS_RICH else "  WARN"
SKIP = "[dim]  SKIP[/dim]" if _HAS_RICH else "  SKIP"


def _print(msg: str) -> None:
    if _HAS_RICH:
        console.print(msg)  # type: ignore[union-attr]
    else:
        # Strip basic rich markup
        import re
        print(re.sub(r"\[/?[^\]]+\]", "", msg))


# ─────────────────────────────────────────────────────────────────────────────
# Check result accumulation
# ─────────────────────────────────────────────────────────────────────────────

_results: List[Tuple[str, str, str]] = []  # (category, label, status_line)


def _record(category: str, label: str, ok: bool, detail: str = "", warn: bool = False) -> bool:
    badge = WARN if warn else (PASS if ok else FAIL)
    line = f"{badge}  {label}"
    if detail:
        dim = "[dim]" if _HAS_RICH else ""
        undim = "[/dim]" if _HAS_RICH else ""
        line += f"  {dim}({detail}){undim}"
    _results.append((category, label, badge))
    _print(line)
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Check: Python version
# ─────────────────────────────────────────────────────────────────────────────

def check_python_version() -> bool:
    v = sys.version_info
    ok = v >= (3, 9)
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    return _record("Environment", "Python version", ok,
                   detail=f"{ver_str} {'≥ 3.9 ✓' if ok else '— requires 3.9+'}")


# ─────────────────────────────────────────────────────────────────────────────
# Check: Required packages
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_PACKAGES = [
    ("dotenv", "python-dotenv"),
    ("httpx", "httpx"),
    ("websockets", "websockets"),
    ("requests", "requests"),
    ("rich", "rich"),
    ("py_clob_client", "py-clob-client"),
]


def check_packages() -> bool:
    all_ok = True
    for import_name, pkg_name in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            _record("Packages", pkg_name, True)
        except ImportError:
            _record("Packages", pkg_name, False, detail=f"pip install {pkg_name}")
            all_ok = False
    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# Check: .env file + required variables
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_LIVE_VARS = ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER"]
_OPTIONAL_VARS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "OPENCLAW_API_KEY", "OPENCLAW_AGENT_ID",
]


def check_env_file() -> bool:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    exists = os.path.isfile(env_path)
    _record("Config", ".env file", exists,
            detail=".env found" if exists else "copy .env.example → .env")
    if not exists:
        return False

    # Load without dotenv import error
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        pass  # dotenv not installed — covered by check_packages

    return True


def check_env_variables() -> bool:
    paper_mode = os.getenv("PAPER_MODE", "true").lower() == "true"

    _record("Config", f"PAPER_MODE", True, detail=str(paper_mode))

    all_ok = True
    if not paper_mode:
        for var in _REQUIRED_LIVE_VARS:
            val = os.getenv(var, "")
            ok = bool(val) and val not in ("0xYOUR_PRIVATE_KEY_HERE", "0xYOUR_WALLET_ADDRESS_HERE")
            all_ok = _record("Config", var, ok,
                             detail="set" if ok else "NOT SET — required for live mode") and all_ok
    else:
        _print(f"  [dim]  (live-mode credential checks skipped — PAPER_MODE=true)[/dim]"
               if _HAS_RICH else "  (live-mode credential checks skipped — PAPER_MODE=true)")

    # Validate MARKETS
    markets_raw = os.getenv("MARKETS", "btc,eth,sol")
    valid = {"btc", "eth", "sol", "xrp"}
    markets = [m.strip().lower() for m in markets_raw.split(",") if m.strip().lower() in valid]
    markets_ok = len(markets) > 0
    _record("Config", "MARKETS", markets_ok, detail=", ".join(markets) if markets_ok else "invalid — use btc/eth/sol/xrp")

    # Optional: Telegram
    tg_enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
    if tg_enabled:
        tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cid = os.getenv("TELEGRAM_CHAT_ID", "")
        tg_ok = bool(tok) and tok != "YOUR_BOT_TOKEN_HERE" and bool(cid) and cid != "YOUR_NUMERIC_CHAT_ID_HERE"
        _record("Config", "Telegram credentials", tg_ok,
                detail="configured" if tg_ok else "BOT_TOKEN or CHAT_ID not set",
                warn=not tg_ok)
    else:
        _record("Config", "Telegram credentials", True, detail="disabled (TELEGRAM_ENABLED=false)")

    # Optional: OpenClaw
    oc_enabled = os.getenv("OPENCLAW_ENABLED", "false").lower() == "true"
    if oc_enabled:
        key = os.getenv("OPENCLAW_API_KEY", "")
        oc_ok = bool(key) and key != "YOUR_OPENCLAW_API_KEY_HERE"
        _record("Config", "OpenClaw API key", oc_ok,
                detail="configured" if oc_ok else "OPENCLAW_API_KEY not set",
                warn=not oc_ok)
    else:
        _record("Config", "OpenClaw credentials", True, detail="disabled (OPENCLAW_ENABLED=false)")

    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# Check: Connectivity
# ─────────────────────────────────────────────────────────────────────────────

def _tcp_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def check_connectivity() -> bool:
    all_ok = True

    # DNS
    try:
        socket.getaddrinfo("clob.polymarket.com", 443)
        _record("Network", "DNS resolution", True, detail="clob.polymarket.com")
    except socket.gaierror as e:
        _record("Network", "DNS resolution", False, detail=str(e))
        all_ok = False

    # Polymarket CLOB API (HTTPS port)
    pm_ok = _tcp_reachable("clob.polymarket.com", 443)
    _record("Network", "Polymarket CLOB API", pm_ok,
            detail="reachable" if pm_ok else "unreachable — check firewall/internet")
    all_ok = all_ok and pm_ok

    # Binance WebSocket endpoint
    bn_ok = _tcp_reachable("stream.binance.com", 9443)
    _record("Network", "Binance stream", bn_ok,
            detail="reachable" if bn_ok else "unreachable — check firewall/internet")
    all_ok = all_ok and bn_ok

    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# Check: Polymarket API responds
# ─────────────────────────────────────────────────────────────────────────────

async def _async_check_polymarket_api() -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://clob.polymarket.com/markets?limit=1")
            ok = resp.status_code == 200
            return _record("API", "Polymarket CLOB /markets", ok,
                           detail=f"HTTP {resp.status_code}")
    except Exception as e:
        return _record("API", "Polymarket CLOB /markets", False, detail=str(e))


def check_polymarket_api() -> bool:
    return asyncio.run(_async_check_polymarket_api())


# ─────────────────────────────────────────────────────────────────────────────
# Check: Telegram (optional live ping)
# ─────────────────────────────────────────────────────────────────────────────

async def _async_check_telegram() -> bool:
    tg_enabled = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
    if not tg_enabled:
        _record("API", "Telegram getMe", True, detail="skipped (disabled)")
        return True

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        _record("API", "Telegram getMe", True, detail="skipped (no token)", warn=False)
        return True

    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            data = resp.json()
            ok = resp.status_code == 200 and data.get("ok", False)
            bot_name = data.get("result", {}).get("username", "?") if ok else ""
            return _record("API", "Telegram getMe", ok,
                           detail=f"@{bot_name}" if ok else data.get("description", "invalid token"),
                           warn=not ok)
    except Exception as e:
        return _record("API", "Telegram getMe", False, detail=str(e), warn=True)


def check_telegram() -> bool:
    return asyncio.run(_async_check_telegram())


# ─────────────────────────────────────────────────────────────────────────────
# Check: Bot config can be loaded + validated
# ─────────────────────────────────────────────────────────────────────────────

def check_bot_config() -> bool:
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        from config import BotConfig
        cfg = BotConfig()
        cfg.validate()
        return _record("Config", "BotConfig.validate()", True, detail="all constraints satisfied")
    except ValueError as e:
        return _record("Config", "BotConfig.validate()", False, detail=str(e))
    except Exception as e:
        return _record("Config", "BotConfig import", False, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    if _HAS_RICH:
        console.rule(f"[bold]{title}[/bold]")  # type: ignore[union-attr]
    else:
        print(f"\n{'─' * 60}")
        print(f"  {title}")
        print(f"{'─' * 60}")


def main() -> int:
    if _HAS_RICH:
        console.print(Panel(  # type: ignore[union-attr]
            "[bold cyan]POLYMARKET[/bold cyan] [white]by Genoshide[/white]  ·  "
            "[dim]polymarket arbitrage script bot[/dim]\n"
            "[bold]Pre-flight Health Check[/bold]",
            box=box.DOUBLE_EDGE,
            padding=(0, 2),
        ))
    else:
        print("\n" + "═" * 62)
        print("  POLYMARKET by Genoshide  ·  Pre-flight Health Check")
        print("═" * 62)

    start = time.monotonic()
    failures = 0

    _section("Environment")
    if not check_python_version():
        failures += 1

    _section("Packages")
    if not check_packages():
        failures += 1

    _section("Configuration")
    env_ok = check_env_file()
    if not env_ok:
        failures += 1
    else:
        if not check_env_variables():
            failures += 1
        if not check_bot_config():
            failures += 1

    _section("Network")
    if not check_connectivity():
        failures += 1

    _section("API Endpoints")
    if not check_polymarket_api():
        failures += 1
    check_telegram()  # soft check — warning only

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start
    total = len(_results)
    passed = sum(1 for _, _, badge in _results if "PASS" in badge)
    warned = sum(1 for _, _, badge in _results if "WARN" in badge)
    failed = total - passed - warned

    _section("Summary")
    if _HAS_RICH:
        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        tbl.add_column(style="dim")
        tbl.add_column()
        tbl.add_row("Checks run:", f"{total}")
        tbl.add_row("Passed:", f"[green]{passed}[/green]")
        if warned:
            tbl.add_row("Warnings:", f"[yellow]{warned}[/yellow]")
        if failed:
            tbl.add_row("Failed:", f"[bold red]{failed}[/bold red]")
        tbl.add_row("Elapsed:", f"{elapsed:.2f}s")
        console.print(tbl)  # type: ignore[union-attr]
    else:
        print(f"  Checks run : {total}")
        print(f"  Passed     : {passed}")
        if warned:
            print(f"  Warnings   : {warned}")
        if failed:
            print(f"  Failed     : {failed}")
        print(f"  Elapsed    : {elapsed:.2f}s")

    if failures == 0:
        msg = ("[bold green]All checks passed.[/bold green] "
               "Run [cyan]make paper[/cyan] to start the bot in paper mode.")
        _print("\n" + msg)
    else:
        msg = (f"[bold red]{failures} check(s) failed.[/bold red] "
               "Fix the issues above before starting the bot.")
        _print("\n" + msg)
        _print("  See [cyan]README.md[/cyan] or [cyan].env.example[/cyan] for configuration help.")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
