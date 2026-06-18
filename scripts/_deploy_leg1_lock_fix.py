#!/usr/bin/env python3
"""Deploy leg1 inflight lock fix to VPS."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, KILL_STALE_BUILD, PROJ, ROOT, USER, load_password

UPLOAD = [
    "trading-core/src/risk/RiskManager.h",
    "trading-core/src/risk/RiskManager.cpp",
    "trading-core/src/signals/LegInHedgeDetector.cpp",
    "trading-core/src/main.cpp",
]


def ro(c, cmd, t=120):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=60)
    tr = c.get_transport()
    if tr:
        tr.set_keepalive(15)
    try:
        print("=== kill stale builds ===", flush=True)
        print(ro(c, KILL_STALE_BUILD, t=30), flush=True)
        sftp = c.open_sftp()
        for rel in UPLOAD:
            print(f"upload {rel}", flush=True)
            sftp.put(str(ROOT / rel), f"{PROJ}/{rel.replace(chr(92), '/')}")
        sftp.close()
        print("\n=== build ===", flush=True)
        out = ro(c, f"cd '{PROJ}' && bash build-lowmem.sh 2>&1 | tail -20", t=900)
        print(out, flush=True)
        if "build-lowmem OK" not in out:
            print("BUILD FAILED", flush=True)
            return 1
        print("\n=== restart bot ===", flush=True)
        ro(
            c,
            f"pkill -f 'start_bot.py' 2>/dev/null || true; pkill -f trading-core 2>/dev/null || true; sleep 2; "
            f"cd '{PROJ}' && nohup .venv/bin/python -u start_bot.py >> logs/bridge.log 2>&1 &",
            t=30,
        )
        time.sleep(8)
        print(ro(c, f"tail -5 '{PROJ}/bot.log'"), flush=True)
        print(ro(c, f"stat -c '%y' '{PROJ}/build/trading-core'"), flush=True)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
