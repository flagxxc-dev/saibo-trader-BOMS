#!/usr/bin/env python3
"""Deploy fetch_balance fix + uncomment POLYMARKET_FUNDER on VPS."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, ROOT, load_password, HOST, USER, run


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    sftp = client.open_sftp()
    for rel in ["fetch_balance.py", "dashboard_bridge.py"]:
        sftp.put(str(ROOT / rel), f"{PROJ}/{rel}")
    sftp.close()

    steps = [
        f"sed -i 's/^#POLYMARKET_FUNDER=/POLYMARKET_FUNDER=/' '{PROJ}/.env'",
        f"cd '{PROJ}' && .venv/bin/python scripts/_fix_funder_env.py",
        f"cd '{PROJ}' && .venv/bin/python fetch_balance.py --json 2>&1",
        f"cd '{PROJ}' && .venv/bin/python fetch_balance.py 2>&1",
    ]
    try:
        for step in steps:
            print(f"\n>>> {step}\n")
            print(run(client, step, timeout=120))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
