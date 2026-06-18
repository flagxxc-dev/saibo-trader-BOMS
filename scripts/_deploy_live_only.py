#!/usr/bin/env python3
"""Deploy live-only purge + rebuild on VPS."""
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password, run

UPLOAD = [
    "trading-core/src/risk/RiskManager.h",
    "trading-core/src/risk/RiskManager.cpp",
    "trading-core/src/state/PaperStateStore.h",
    "trading-core/src/state/PaperStateStore.cpp",
    "trading-core/src/state/StateStore.cpp",
    "trading-core/src/main.cpp",
    "dashboard_bridge.py",
    "start_bot.py",
    "redeem_positions.py",
]


def main() -> int:
    print("deploy live-only: connecting...", flush=True)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)
    sftp = client.open_sftp()
    for rel in UPLOAD:
        print(f"upload {rel}", flush=True)
        sftp.put(str(Path(__file__).resolve().parents[1] / rel), f"{PROJ}/{rel.replace(chr(92), '/')}")
    sftp.close()

    steps = [
        f"rm -f '{PROJ}/logs/paper_state.json' '{PROJ}/logs/paper_state.json.tmp' 2>/dev/null; echo ok",
        f"grep -q '^PAPER_MODE=' '{PROJ}/.env' && sed -i 's/^PAPER_MODE=.*/PAPER_MODE=false/' '{PROJ}/.env' "
        f"|| echo 'PAPER_MODE=false' >> '{PROJ}/.env'",
        f"python3 -c \"import json,pathlib; p=pathlib.Path('{PROJ}/logs/live_state.json'); "
        "d=json.loads(p.read_text()) if p.is_file() else {{}}; "
        "d['open_lih_positions']={{}}; d['lih_session_legs_used']=0; "
        "p.write_text(json.dumps(d,separators=(',',':'))); print('live_state open cleared')\" "
        "2>/dev/null || echo no live_state",
        "pkill -f trading-core || true; pkill -f start_bot.py || true; sleep 2",
        f"cd '{PROJ}' && bash build-lowmem.sh 2>&1 | tail -30",
        f"bash '{PROJ}/server_start_bot.sh'",
        "sleep 12",
        "pgrep -af 'start_bot|trading-core' || true",
        f"curl -s http://127.0.0.1:8081/api/config | python3 -c "
        "\"import sys,json; l=json.load(sys.stdin).get('live',{}); "
        "print('openCount', l.get('openCount'), 'paper', l.get('isPaperMode'))\"",
    ]
    for step in steps:
        print(f">>> {step[:90]}")
        print(run(client, step, timeout=1800))
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
