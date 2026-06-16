#!/usr/bin/env python3
"""Emergency: pause live trading + switch .env to paper (no build)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)

    def run(cmd: str) -> str:
        _, o, e = c.exec_command(cmd, timeout=60)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    # 1. Pause immediately
    pause = json.dumps({"control": "pause", "reason": "user emergency stop — paper only"})
    print(run(f"printf '%s' '{pause}' > '{PROJ}/logs/runtime_config.json'"))

    # 2. Paper mode in .env (no real orders on next restart)
    for key, val in [
        ("PAPER_MODE", "true"),
        ("LIVE_LIH_DRY_RUN", "true"),
        ("RISK_MAX_CONCURRENT_POSITIONS", "0"),
    ]:
        run(
            f"grep -q '^{key}=' '{PROJ}/.env' && "
            f"sed -i 's/^{key}=.*/{key}={val}/' '{PROJ}/.env' || "
            f"echo '{key}={val}' >> '{PROJ}/.env'"
        )

    print("=== env ===")
    print(run(f"grep -E '^PAPER_MODE=|^LIVE_LIH_DRY_RUN=|^RISK_MAX_CONCURRENT' '{PROJ}/.env'"))

    print("=== status ===")
    print(run(
        "sleep 2; curl -s http://127.0.0.1:8081/api/config | python3 -c "
        "'import sys,json; l=json.load(sys.stdin).get(\"live\",{}); "
        "print(\"status\", l.get(\"status\"), l.get(\"statusReason\",\"\")); "
        "print(\"paperMode\", l.get(\"paperMode\")); print(\"openCount\", l.get(\"openCount\"))'"
    ))

    print("=== processes (bot still runs for web, trading paused) ===")
    print(run("pgrep -af 'start_bot|trading-core' || echo STOPPED"))

    c.close()
    print("\n已停：实盘暂停，.env 已改纸面。未编译、未 resume。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
