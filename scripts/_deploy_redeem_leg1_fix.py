#!/usr/bin/env python3
"""Deploy redeem + leg1 lock fixes to VPS."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, KILL_STALE_BUILD, PROJ, ROOT, USER, load_password  # noqa: E402

UPLOAD = [
    "redeem_positions.py",
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

        print("\n=== upload ===", flush=True)
        sftp = c.open_sftp()
        for rel in UPLOAD:
            remote = f"{PROJ}/{rel.replace(chr(92), '/')}"
            print(f"  {rel}", flush=True)
            sftp.put(str(ROOT / rel), remote)
        sftp.close()

        print("\n=== verify redeem script (no-op check) ===", flush=True)
        cid = ro(
            c,
            f"grep -a 'condition 0x' '{PROJ}/bot.log' 2>/dev/null | tail -1 | "
            r"grep -oE '0x[a-fA-F0-9]{64}' | tail -1",
            t=15,
        )
        if cid.startswith("0x"):
            print(
                ro(
                    c,
                    f"cd '{PROJ}' && .venv/bin/python redeem_positions.py \"{cid}\" true",
                    t=45,
                ),
                flush=True,
            )
        else:
            print("  skip — no recent condition_id in bot.log", flush=True)

        print("\n=== build core ===", flush=True)
        print(
            ro(
                c,
                f"cd '{PROJ}' && bash build-lowmem.sh 2>&1 | tail -25",
                t=900,
            ),
            flush=True,
        )
        tail = ro(c, f"tail -3 '{PROJ}/build/build.log' 2>/dev/null", t=10)
        if "build-lowmem OK" not in tail and "Linking CXX executable trading-core" not in ro(
            c, f"tail -8 '{PROJ}/build/build.log' 2>/dev/null", t=10
        ):
            print(f"BUILD FAILED:\n{tail}", flush=True)
            return 1

        print("\n=== restart bot ===", flush=True)
        print(ro(c, f"bash '{PROJ}/server_restart_bot.sh'", t=120), flush=True)
        time.sleep(6)
        print(ro(c, f"tail -8 '{PROJ}/bot.log'", t=15), flush=True)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
