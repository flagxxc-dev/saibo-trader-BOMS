#!/usr/bin/env python3
"""Upload shadow log fix (OrderRouter + dashboard_bridge) and rebuild."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, ROOT, USER, load_password, run  # noqa: E402


def run_out(c, cmd, t=120):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    try:
        sftp = c.open_sftp()
        for local, remote in [
            (ROOT / "trading-core/src/exec/OrderRouter.cpp", f"{PROJ}/trading-core/src/exec/OrderRouter.cpp"),
            (ROOT / "dashboard_bridge.py", f"{PROJ}/dashboard_bridge.py"),
        ]:
            print(f"Upload {local.name}")
            sftp.put(str(local), remote)
        sftp.close()
        run_out(c, f"cd '{PROJ}' && ./build.sh", timeout=1800)
        run_out(c, f"sed -i 's/^#POLYMARKET_PRIVATE_KEY=/POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'")
        run(c, f"bash '{PROJ}/server_start_bot.sh'", timeout=120)
        time.sleep(12)
        run_out(c, f"grep -aF '[LIVE LIH SHADOW]' '{PROJ}/logs/bridge.log' | tail -5 || true")
        run_out(c, f"sed -i 's/^POLYMARKET_PRIVATE_KEY=/#POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'")
        print("Done — next LEG1 shadow will land in bridge.log")
    finally:
        c.close()


if __name__ == "__main__":
    main()
