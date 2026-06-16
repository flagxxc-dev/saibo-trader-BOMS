#!/usr/bin/env python3
"""Deploy web UI sync fix (frontend build + bridge patch)."""
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
    files = [
        "frontend/src/lib/coreStatus.ts",
        "frontend/src/hooks/useLiveState.ts",
        "frontend/src/app/strategies/page.tsx",
        "frontend/src/app/dashboard/page.tsx",
        "dashboard_bridge.py",
    ]
    for rel in files:
        local = ROOT / rel
        remote = f"{PROJ}/{rel.replace(chr(92), '/')}"
        print(f"Upload {rel}")
        sftp.put(str(local), remote)
    sftp.close()

    steps = [
        f"cd '{PROJ}/frontend' && export NODE_OPTIONS='--max-old-space-size=512' && npm run build",
        f"bash '{PROJ}/server_restart_web.sh'",
        "sleep 3",
        "pgrep -af 'node.*server.js|next' | head -3 || echo web_down",
    ]
    try:
        for step in steps:
            print(f"\n>>> {step}\n")
            r = run(client, step, timeout=1800)
            if r != 0 and "npm run build" in step:
                return r
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
