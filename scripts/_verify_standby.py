#!/usr/bin/env python3
"""Verify VPS is in safe standby — no live orders possible. Read-only checks + safe restart test."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER, run


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    ok = True
    try:
        checks = [
            ("STOP_TRADING flag", f"test -f '{PROJ}/logs/STOP_TRADING' && echo YES || echo NO"),
            ("PAPER_MODE", f"grep '^PAPER_MODE=' '{PROJ}/.env'"),
            ("LIH_ENABLED", f"grep '^LIH_ENABLED=' '{PROJ}/.env'"),
            ("RISK_MAX_CONCURRENT", f"grep '^RISK_MAX_CONCURRENT_POSITIONS=' '{PROJ}/.env'"),
            ("PRIVATE_KEY commented", f"grep -n 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | head -3"),
            ("FUNDER", f"grep '^POLYMARKET_FUNDER=' '{PROJ}/.env'"),
            ("processes", "pgrep -af 'start_bot|trading-core|build.sh|ninja' || echo NONE"),
            ("binary strings old", f"strings '{PROJ}/build/trading-core' 2>/dev/null | grep -F 'released lock, run reconcile' | head -1 || echo OLD_STRING=absent"),
            ("binary strings new", f"strings '{PROJ}/build/trading-core' 2>/dev/null | grep -F 'keeping in-flight lock' | head -1 || echo NEW_STRING=absent"),
            ("bridge log pending", f"grep -aE 'keeping in-flight|released lock|LEG1 pending' '{PROJ}/logs/bridge.log' 2>/dev/null | tail -5 || echo NO_LIH_LOGS"),
        ]
        for label, cmd in checks:
            print(f"\n=== {label} ===")
            out = run(client, cmd, timeout=60)
            print(out)
            if label == "STOP_TRADING flag" and "YES" not in str(out):
                print("FAIL: STOP_TRADING missing")
                ok = False
            if label == "PAPER_MODE" and "PAPER_MODE=true" not in str(out):
                print("FAIL: not paper mode")
                ok = False
            if label == "PRIVATE_KEY commented":
                text = str(out)
                if "POLYMARKET_PRIVATE_KEY=" in text and not text.strip().startswith("#"):
                    for line in text.splitlines():
                        s = line.strip()
                        if "POLYMARKET_PRIVATE_KEY=" in s and not s.lstrip().startswith("#"):
                            if "PRIVATE_KEY=" in s and not s.startswith("#"):
                                print("FAIL: private key line appears active")
                                ok = False

        print("\n=== restart bridge (paper + STOP_TRADING, key untouched) ===")
        print(run(client, f"bash '{PROJ}/server_start_bot.sh'", timeout=90))
        time.sleep(8)
        print(run(client, "pgrep -af 'start_bot|trading-core' || echo NONE", timeout=30))
        print(run(client, f"tail -20 '{PROJ}/logs/bridge.log'", timeout=30))
        tail = run(client, f"tail -5 '{PROJ}/logs/bridge.log'", timeout=30)
        if "STOP_TRADING" not in str(tail) and "PAUSED" not in str(tail):
            # core ready line may mention STOP_TRADING
            full = run(client, f"grep -a 'STOP_TRADING\\|PAUSED\\|STOP_TRADING flag' '{PROJ}/logs/bridge.log' | tail -3", timeout=30)
            print(full)
            if "STOP_TRADING" not in str(full) and "PAUSED" not in str(full):
                print("WARN: could not confirm paused state in recent logs")

        print("\n=== fetch_balance (read-only) ===")
        print(run(client, f"cd '{PROJ}' && .venv/bin/python fetch_balance.py --json", timeout=90))

        print(f"\n=== OVERALL: {'PASS (standby)' if ok else 'FAIL'} ===")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
