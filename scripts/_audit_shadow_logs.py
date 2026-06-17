#!/usr/bin/env python3
"""Audit overnight LIVE LIH shadow logs on VPS."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    return (stdout.read() + stderr.read()).decode(errors="replace").strip()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    log = f"{PROJ}/logs/bridge.log"
    try:
        sections = [
            ("ENV", f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED|START_SKIP)' '{PROJ}/.env'"),
            (
                "KEY (.env)",
                f"grep 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | sed 's/=.*/=***REDACTED***/' | head -1",
            ),
            ("STOP_TRADING", f"test -f '{PROJ}/logs/STOP_TRADING' && echo yes || echo no"),
            ("PROCESSES", "pgrep -af 'start_bot|trading-core' || echo NONE"),
            ("CORE uptime", "ps -p $(pgrep -n trading-core 2>/dev/null) -o etime=,cmd= 2>/dev/null || echo unknown"),
            ("CORE READY (last 3)", f"grep -a 'CORE 就绪' '{log}' | tail -3"),
            (
                "Real SHADOW dry_run count",
                f"grep -aF 'dry_run' '{log}' | grep -aF 'SHADOW' | wc -l",
            ),
            ("Sample dry_run shadow", f"grep -aF 'dry_run' '{log}' | grep -aF 'SHADOW' | tail -8"),
            ("LIH SHADOW signal lines", f"grep -aF '[LIH SHADOW]' '{log}' | tail -10"),
            ("LEG1 skip/blocked recent", f"grep -aE 'LEG1 skip|LEG1 blocked|LIH entry' '{log}' | tail -12"),
            ("MARKETS REFRESHED recent", f"grep -a 'MARKETS REFRESHED' '{log}' | tail -5"),
            (
                "LIVE EXEC since live LIH boot",
                f"awk '/CORE 就绪.*实盘.*LIH/{{f=1}} f' '{log}' | grep -cF '[LIVE EXEC]'",
            ),
            (
                "Activity since live LIH boot",
                f"awk '/CORE 就绪.*实盘.*LIH/{{f=1}} f' '{log}' | grep -aE 'SHADOW|LEG1|HEDGE|skip|blocked|MARKETS' | tail -30",
            ),
            ("LAST 10 real SHADOW", f"grep -aF '[LIVE LIH SHADOW]' '{log}' | grep -v '出现' | tail -10"),
            (
                "WARN duplicate/pending",
                f"grep -aE 'released lock|keeping in-flight|duplicate LEG1|CRITICAL unwind' '{log}' | tail -10",
            ),
            ("RECENT tail", f"tail -8 '{log}'"),
        ]
        ok = True
        real_shadow = 0
        for title, cmd in sections:
            out = run(client, cmd)
            print(f"\n=== {title} ===\n{out}")
            if title == "PROCESSES" and "trading-core" not in out:
                ok = False
            if title == "ENV":
                if "PAPER_MODE=false" not in out or "LIVE_LIH_DRY_RUN=true" not in out:
                    ok = False
            if title == "STOP_TRADING" and out.strip() == "yes":
                ok = False
            if title == "Real SHADOW dry_run count":
                try:
                    real_shadow = int(out.strip().split()[0])
                except (ValueError, IndexError):
                    real_shadow = 0
            if title == "LIVE EXEC since live LIH boot":
                try:
                    if int(out.strip() or "0") > 0:
                        print("WARN: LIVE EXEC since shadow boot")
                        ok = False
                except ValueError:
                    pass

        if real_shadow < 1:
            print("\nNOTE: 整晚无真实 shadow 成交信号（dry_run 行=0）— 可能市场未触发 leg1 条件")
            # infra still OK if process up and no live exec
        else:
            print(f"\n真实 shadow 执行（含 dry_run）: {real_shadow} 条")

        print(f"\n=== SHADOW AUDIT: {'PASS' if ok else 'NEEDS REVIEW'} ===")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
