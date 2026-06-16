#!/usr/bin/env python3
"""Post fix-build: fix funder, restart bridge, verify standby (no key changes)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, ROOT, load_password, HOST, USER, run


def run_out(client: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    combined = (out + err).strip()
    print(f"\n>>> {cmd}\n{combined}\n")
    return combined


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    sftp = client.open_sftp()
    sftp.put(str(ROOT / "scripts/_fix_funder_env.py"), f"{PROJ}/scripts/_fix_funder_env.py")
    sftp.close()

    ok = True
    try:
        stop = run_out(client, f"test -f '{PROJ}/logs/STOP_TRADING' && echo STOP=yes || echo STOP=no")
        if "STOP=yes" not in stop:
            ok = False
        paper = run_out(client, f"grep '^PAPER_MODE=' '{PROJ}/.env'")
        if "PAPER_MODE=true" not in paper:
            ok = False
        key = run_out(client, f"grep 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | head -1")
        if key.strip() and not key.strip().startswith("#"):
            ok = False
            print("FAIL: private key line not commented")

        run_out(client, f"cd '{PROJ}' && .venv/bin/python scripts/_fix_funder_env.py")
        run(client, f"bash '{PROJ}/server_start_bot.sh'", timeout=90)
        time.sleep(8)

        procs = run_out(client, "pgrep -af 'start_bot|trading-core' || echo NONE")
        if "trading-core" not in procs:
            ok = False
        strings = run_out(
            client,
            f"strings '{PROJ}/build/trading-core' | grep -E 'keeping in-flight|released lock' | head -5",
        )
        if "keeping in-flight lock" not in strings:
            ok = False
        if "released lock, run reconcile" in strings:
            ok = False
            print("FAIL: old binary string still present")
        core = run_out(client, f"grep -a 'CORE 就绪' '{PROJ}/logs/bridge.log' | tail -1")
        if "STOP_TRADING" not in core and "PAUSED" not in core:
            ok = False
        run_out(client, f"cd '{PROJ}' && .venv/bin/python fetch_balance.py --json")

        print(f"\n=== POST-DEPLOY STANDBY: {'PASS' if ok else 'FAIL'} ===")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
