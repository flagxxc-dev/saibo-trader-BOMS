#!/usr/bin/env python3
"""Restart bot after build completes."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402


def ro(c, cmd, t=60):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=60)
    try:
        print("=== RESTART ===", flush=True)
        print(ro(c, f"bash '{PROJ}/server_start_bot.sh'", t=90), flush=True)
        time.sleep(8)
        print("=== PROCS ===", flush=True)
        print(ro(c, "pgrep -af 'start_bot|trading-core'"), flush=True)
        print("=== BINARY MTIME ===", flush=True)
        print(ro(c, f"stat -c '%y %s' '{PROJ}/build/trading-core'"), flush=True)
        print("=== BOOT LOG ===", flush=True)
        print(ro(c, f"grep -a 'Starting Core' '{PROJ}/logs/bridge.log' | tail -1"), flush=True)
        print("=== API ===", flush=True)
        print(
            ro(
                c,
                "curl -s -m 10 http://127.0.0.1:8081/api/config | python3 -c \"import sys,json; "
                "l=json.load(sys.stdin).get('live',{}); "
                "print('dryRun', l.get('liveLihDryRun')); "
                "print('riskMax', l.get('riskMaxConcurrentPositions')); "
                "print('balance', l.get('balance')); "
                "print('open', l.get('openCount'))\"",
            ),
            flush=True,
        )
    finally:
        c.close()


if __name__ == "__main__":
    main()
