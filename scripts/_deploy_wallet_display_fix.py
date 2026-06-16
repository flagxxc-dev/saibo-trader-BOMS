#!/usr/bin/env python3
"""Deploy wallet balance display fix (bridge + frontend)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, ROOT, load_password, HOST, USER, run


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    sftp = client.open_sftp()
    files = [
        "dashboard_bridge.py",
        "frontend/src/hooks/useLiveState.ts",
        "frontend/src/app/dashboard/page.tsx",
    ]
    for rel in files:
        local = ROOT / rel
        remote = f"{PROJ}/{rel.replace(chr(92), '/')}"
        print(f"Upload {rel}")
        sftp.put(str(local), remote)
    sftp.close()

    steps = [
        f"grep '^POLYMARKET_FUNDER=' '{PROJ}/.env'",
        f"cd '{PROJ}/frontend' && export NODE_OPTIONS='--max-old-space-size=512' && npm run build",
        f"bash '{PROJ}/server_restart_web.sh'",
        f"bash '{PROJ}/server_start_bot.sh'",
    ]
    try:
        for step in steps:
            print(f"\n>>> {step}\n")
            r = run(client, step, timeout=1800)
            if r != 0 and "npm run build" in step:
                return r
        time.sleep(8)
        print(run(client, f"tail -15 '{PROJ}/logs/bridge.log'", timeout=30))
        print(run(client, f"pgrep -af 'start_bot|trading-core|node.*server' | head -5", timeout=30))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
