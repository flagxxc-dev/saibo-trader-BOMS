#!/usr/bin/env python3
"""Deploy pending/reconcile fixes + step1 shadow overnight (no real orders).

Temporarily uncomments POLYMARKET_PRIVATE_KEY so core can boot in live shadow mode,
then comments it back in .env after restart (running process keeps loaded key until restart).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, ROOT, USER, load_password, run  # noqa: E402


def run_out(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
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

    uploads = [
        (ROOT / "trading-core/src/exec/OrderRouter.cpp", f"{PROJ}/trading-core/src/exec/OrderRouter.cpp"),
        (ROOT / "trading-core/src/exec/OrderRouter.h", f"{PROJ}/trading-core/src/exec/OrderRouter.h"),
        (ROOT / "trading-core/src/main.cpp", f"{PROJ}/trading-core/src/main.cpp"),
        (ROOT / "start_bot.py", f"{PROJ}/start_bot.py"),
        (ROOT / "scripts/live_lih_reconcile.py", f"{PROJ}/scripts/live_lih_reconcile.py"),
        (ROOT / "scripts/_check_overnight.py", f"{PROJ}/scripts/_check_overnight.py"),
    ]

    ok = True
    try:
        sftp = client.open_sftp()
        for local, remote in uploads:
            print(f"Upload {local.name} -> {remote}")
            sftp.put(str(local), remote)
        sftp.close()

        run_out(client, f"cd '{PROJ}' && chmod +x build.sh && ./build.sh", timeout=1800)

        env_cmds = (
            f"sed -i 's/^#POLYMARKET_PRIVATE_KEY=/POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'; "
            f"sed -i 's/^PAPER_MODE=.*/PAPER_MODE=false/' '{PROJ}/.env'; "
            f"grep -q '^LIVE_LIH_DRY_RUN=' '{PROJ}/.env' && "
            f"sed -i 's/^LIVE_LIH_DRY_RUN=.*/LIVE_LIH_DRY_RUN=true/' '{PROJ}/.env' || "
            f"echo 'LIVE_LIH_DRY_RUN=true' >> '{PROJ}/.env'; "
            f"grep -q '^LIH_ENABLED=' '{PROJ}/.env' && "
            f"sed -i 's/^LIH_ENABLED=.*/LIH_ENABLED=true/' '{PROJ}/.env' || "
            f"echo 'LIH_ENABLED=true' >> '{PROJ}/.env'; "
            f"grep -q '^START_SKIP_PRELIVE=' '{PROJ}/.env' && "
            f"sed -i 's/^START_SKIP_PRELIVE=.*/START_SKIP_PRELIVE=true/' '{PROJ}/.env' || "
            f"echo 'START_SKIP_PRELIVE=true' >> '{PROJ}/.env'; "
            f"rm -f '{PROJ}/logs/STOP_TRADING'"
        )
        run_out(client, env_cmds)
        run_out(
            client,
            f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED|START_SKIP_PRELIVE)' '{PROJ}/.env'",
        )
        key_line = run_out(
            client,
            f"grep 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | sed 's/=.*/=***REDACTED***/' | head -1",
        )
        if "POLYMARKET_PRIVATE_KEY=" not in key_line or key_line.strip().startswith("#"):
            print("FAIL: private key not uncommented for shadow boot")
            ok = False

        run(client, f"bash '{PROJ}/server_start_bot.sh'", timeout=120)
        time.sleep(18)

        procs = run_out(client, "pgrep -af 'start_bot|trading-core' || echo NONE")
        if "trading-core" not in procs:
            ok = False

        strings = run_out(
            client,
            f"strings '{PROJ}/build/trading-core' | grep -E 'tracking pending|poll_lih_pending|keeping in-flight' | head -5",
        )
        if "tracking pending" not in strings:
            ok = False

        core = run_out(client, f"grep -a 'CORE 就绪\\|LIH dry-run\\|LIVE LIH SHADOW\\|Mode: LIVE' '{PROJ}/logs/bridge.log' | tail -8")
        if "dry-run" not in core.lower() and "shadow" not in core.lower() and "DRY" not in core:
            tail = run_out(client, f"tail -25 '{PROJ}/logs/bridge.log'")
            if "LIVE LIH SHADOW" not in tail and "dry-run" not in tail.lower():
                print("WARN: shadow markers not yet in log (may need market activity)")

        run_out(client, f"grep -c 'LIVE LIH SHADOW' '{PROJ}/logs/bridge.log' 2>/dev/null || echo 0")

        # Comment key back in .env — running core already loaded it; no new orders (DRY_RUN=true).
        run_out(client, f"sed -i 's/^POLYMARKET_PRIVATE_KEY=/#POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'")
        key_after = run_out(
            client,
            f"grep 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | sed 's/=.*/=***REDACTED***/' | head -1",
        )
        if not key_after.strip().startswith("#"):
            print("FAIL: could not comment private key back")
            ok = False

        run_out(
            client,
            f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED)' '{PROJ}/.env'; "
            f"test -f '{PROJ}/logs/STOP_TRADING' && echo STOP=yes || echo STOP=no",
        )

        print(f"\n=== STEP1 SHADOW OVERNIGHT: {'PASS' if ok else 'FAIL'} ===")
        print("Shadow running: LIVE_LIH_DRY_RUN=true (no CLOB orders). Key commented in .env again.")
        print("Do NOT restart bot until tomorrow — restart needs key uncommented again.")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
