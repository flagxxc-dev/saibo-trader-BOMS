#!/usr/bin/env python3
"""Quick server status check."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
PROJ = "/opt/polymarket-bot"
HOST = "70.34.221.132"


def load_password() -> str:
    text = (ROOT / ".deploy.local").read_text(encoding="utf-8")
    m = re.search(r'DEPLOY_SSH_PASSWORD\s*=\s*["\'](.+?)["\']', text)
    if not m:
        raise SystemExit("no password in .deploy.local")
    return m.group(1)


def run(cmd: str) -> str:
    _, o, e = client.exec_command(cmd, timeout=60)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    return (out + err).strip()


if __name__ == "__main__":
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=load_password(), timeout=20)
    checks = [
        ("processes", "pgrep -af 'start_bot|trading-core|build.sh|cmake|ninja' || echo '(no matching processes)'"),
        ("binary", f"ls -la {PROJ}/build/trading-core* 2>/dev/null || echo '(no binary)'"),
        ("env", f"grep -E '^(PAPER_MODE|LIVE_DH_DRY_RUN|DH_BOOK_AWARE)' {PROJ}/.env"),
        ("uploaded", f"test -f {PROJ}/trading-core/src/signals/LegInHedgeDetector.cpp && echo LIH=ok || echo LIH=missing"),
        ("build_log", f"tail -8 {PROJ}/build/build.log 2>/dev/null || echo '(no build.log)'"),
        ("bot_log", f"tail -12 {PROJ}/bot.log 2>/dev/null || echo '(no bot.log)'"),
        ("startup", f"grep -E 'Starting Core|Book-aware detect|Strategy:' {PROJ}/bot.log 2>/dev/null | tail -8 || true"),
        ("detected", f"grep 'DUMP-HEDGE DETECTED' {PROJ}/bot.log 2>/dev/null | tail -5 || true"),
        ("detector_src", f"grep -n 'REST YES' {PROJ}/trading-core/src/signals/DumpHedgeDetector.cpp 2>/dev/null | head -2 || echo '(old detector source)'"),
        ("binary_time", f"stat -c '%y %s bytes' {PROJ}/build/trading-core"),
    ]
    for name, cmd in checks:
        print(f"\n=== {name} ===")
        print(run(cmd))
    client.close()
