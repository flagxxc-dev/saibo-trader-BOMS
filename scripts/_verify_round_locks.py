#!/usr/bin/env python3
"""Post-round: verify no stale leg1/rebalance locks in logs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402

RID = "LIH-btc-1781809735375"


def ro(c, cmd, t=60):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=60)
    try:
        print("=== SCRUB / INFLIGHT ===")
        print(
            ro(
                c,
                f"grep -aE 'scrub|leg1 in-flight|inflight tail|orphan leg1' "
                f"'{PROJ}/bot.log' | tail -20 || echo none",
            )
        )
        print("\n=== AFTER CLOSE ===")
        print(ro(c, f"grep -a -A25 'CLOSED {RID}' '{PROJ}/bot.log' | tail -28"))
        print("\n=== ENTRY-WAIT AFTER CLOSE ===")
        print(
            ro(
                c,
                f"grep -a 'entry-wait.*btc 5m' '{PROJ}/bot.log' | "
                f"grep -a '19:10:' | tail -10 || echo none",
            )
        )
        print("\n=== AUTO-REDEEM ===")
        print(
            ro(
                c,
                f"grep -aE 'AUTO-REDEEM|redeem' '{PROJ}/bot.log' | tail -8",
            )
        )
        print("\n=== API ===")
        raw = ro(c, "curl -s -m 8 http://127.0.0.1:8081/api/config", t=15)
        live = json.loads(raw).get("live", {})
        print(
            json.dumps(
                {
                    "open": live.get("openCount"),
                    "riskMax": live.get("riskMaxConcurrentPositions"),
                    "status": live.get("status"),
                    "reason": live.get("statusReason"),
                    "bal": live.get("balance"),
                },
                indent=2,
            )
        )
    finally:
        c.close()


if __name__ == "__main__":
    main()
