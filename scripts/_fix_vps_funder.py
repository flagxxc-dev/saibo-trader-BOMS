#!/usr/bin/env python3
"""Fix corrupted POLYMARKET_FUNDER on VPS and restart bridge."""
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
    sftp.put(str(ROOT / "scripts/_fix_funder_env.py"), f"{PROJ}/scripts/_fix_funder_env.py")
    sftp.close()
    steps = [
        f"grep 'POLYMARKET_FUNDER' '{PROJ}/.env'",
        f"cd '{PROJ}' && .venv/bin/python scripts/_fix_funder_env.py",
        f"grep 'POLYMARKET_FUNDER' '{PROJ}/.env'",
        f"cd '{PROJ}' && .venv/bin/python fetch_balance.py --json",
        f"bash '{PROJ}/server_start_bot.sh'",
    ]
    try:
        for step in steps:
            print(f"\n>>> {step}\n")
            print(run(client, step, timeout=120))
        time.sleep(6)
        print(run(client, f"grep -a WALLET '{PROJ}/logs/bridge.log' | tail -3"))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
