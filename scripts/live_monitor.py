#!/usr/bin/env python3
"""Poll VPS bot status + tail key LIH events. Run: python scripts/live_monitor.py [--once]"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402

WATCH_PATTERNS = re.compile(
    r"LIH LIVE|LEG1|HEDGE|Bridge fill|entry-wait|below LIH_MIN|"
    r"LEG1 blocked|openCount|Trading halted|PAUSE|pending fill|"
    r"\[LIH LIVE\]|register_lih|session leg"
)


def _connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    return c


def _run(c: paramiko.SSHClient, cmd: str, timeout: int = 45) -> str:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    return (out.read() + err.read()).decode("utf-8", errors="replace").strip()


def snapshot(c: paramiko.SSHClient) -> dict:
    bal_raw = _run(c, f"cd '{PROJ}' && .venv/bin/python fetch_balance.py 2>&1")
    lines = bal_raw.splitlines()
    balance = None
    for line in reversed(lines):
        try:
            balance = float(line.strip())
            break
        except ValueError:
            continue
    api_raw = _run(c, "curl -s http://127.0.0.1:8081/api/config")
    live: dict = {}
    try:
        doc = json.loads(api_raw)
        live = doc.get("live") or doc
    except json.JSONDecodeError:
        live = {"error": api_raw[:120]}
    procs = _run(c, "pgrep -af 'start_bot|trading-core' || echo STOPPED")
    return {
        "balance_chain": balance,
        "live": live,
        "procs": procs,
    }


def print_snapshot(snap: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    live = snap.get("live") or {}
    bal = snap.get("balance_chain")
    bot_bal = live.get("balance")
    print(f"\n=== [{ts}] ===")
    if bal is not None:
        print(f"  链上/CLOB 余额: ${bal:.2f}")
    if bot_bal is not None:
        print(f"  Bot 内存余额:   ${float(bot_bal):.2f}")
    print(f"  持仓 openCount: {live.get('openCount', '?')}")
    print(
        f"  session: {live.get('lihSessionLegsUsed', '?')}/"
        f"{live.get('lihSessionMaxLegs', '?')}  "
        f"minBal=${live.get('lihMinBalanceUsdc', '?')}"
    )
    status = live.get("status", 0)
    reason = live.get("statusReason") or ""
    status_map = {0: "ACTIVE", 1: "PAUSED?", 2: "PAUSED", 3: "KILLED", 4: "DAILY_HALT"}
    print(f"  状态: {status_map.get(status, status)} {reason}")
    ops = live.get("openPositions") or []
    for p in ops:
        print(
            f"  持仓 {p.get('asset')} {p.get('heldSide')} "
            f"Y={p.get('yesSize')} N={p.get('noSize')} gap={p.get('gap')}"
        )
    if "trading-core" not in snap.get("procs", ""):
        print("  ⚠ Bot 进程未运行!")
    min_bal = float(live.get("lihMinBalanceUsdc") or 10)
    effective = float(bot_bal or bal or 0)
    if effective >= min_bal and status == 0 and ops == []:
        print("  ✓ 已就绪 — 等待单边 ≤0.45 自动 leg1")
    elif effective < min_bal:
        print(f"  ✗ 余额不足 ${min_bal:.0f} 门槛")


def tail_new(c: paramiko.SSHClient, since_line: int) -> tuple[list[str], int]:
    raw = _run(c, f"wc -l < '{PROJ}/bot.log' 2>/dev/null || echo 0")
    try:
        total = int(raw.split()[0])
    except (ValueError, IndexError):
        total = 0
    if total <= since_line:
        return [], since_line
    chunk = _run(
        c,
        f"tail -n +{since_line + 1} '{PROJ}/bot.log' 2>/dev/null | tail -n 200",
    )
    hits = [ln for ln in chunk.splitlines() if WATCH_PATTERNS.search(ln)]
    return hits, total


def arm_server(c: paramiko.SSHClient, *, reset_session: bool = False) -> None:
    """Restart bot so deposit is picked up immediately; ensure auto-trade env."""
    env_fixes = [
        ("RISK_MAX_CONCURRENT_POSITIONS", "1"),
        ("LIH_MIN_BALANCE_USDC", "10"),
        ("LIH_ONE_SLOT_GLOBAL", "true"),
        ("LIH_SESSION_MAX_LEGS", "2"),
        ("LIH_PAUSE_AFTER_ROUND", "true"),
        ("LIH_TARGET_COMBINED", "0.95"),
    ]
    for key, val in env_fixes:
        _run(
            c,
            f"grep -q '^{key}=' '{PROJ}/.env' && "
            f"sed -i 's/^{key}=.*/{key}={val}/' '{PROJ}/.env' || "
            f"echo '{key}={val}' >> '{PROJ}/.env'",
        )
    if reset_session:
        _run(
            c,
            f"printf '%s' '{{\"control\":\"reset_lih_session\",\"user\":\"live_monitor\"}}' "
            f"> '{PROJ}/logs/runtime_config.json'",
        )
    _run(c, "pkill -f trading-core || true; pkill -f start_bot.py || true; sleep 2")
    _run(c, f"bash '{PROJ}/server_start_bot.sh'")
    time.sleep(12)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Single snapshot")
    ap.add_argument("--arm", action="store_true", help="Restart bot + reset session")
    ap.add_argument("--interval", type=float, default=20.0)
    args = ap.parse_args()

    c = _connect()
    try:
        if args.arm:
            print("正在重启 bot 以同步充值余额…")
            arm_server(c, reset_session=True)
        snap = snapshot(c)
        print_snapshot(snap)
        if args.once:
            recent = _run(
                c,
                f"grep -E 'LIH|LEG1|HEDGE|Bridge fill|entry-wait|below LIH_MIN' "
                f"'{PROJ}/bot.log' | tail -15",
            )
            if recent:
                print("\n--- 最近日志 ---")
                print(recent)
            return 0

        print(f"\n监控中 (每 {args.interval:.0f}s 刷新，Ctrl+C 停止)…")
        line_no = 0
        raw = _run(c, f"wc -l < '{PROJ}/bot.log' 2>/dev/null || echo 0")
        try:
            line_no = int(raw.split()[0])
        except (ValueError, IndexError):
            line_no = 0

        while True:
            time.sleep(args.interval)
            try:
                c.close()
            except Exception:
                pass
            c = _connect()
            snap = snapshot(c)
            print_snapshot(snap)
            hits, line_no = tail_new(c, line_no)
            for ln in hits[-20:]:
                print(f"  >> {ln[-200:]}")
    except KeyboardInterrupt:
        print("\n监控已停止")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
