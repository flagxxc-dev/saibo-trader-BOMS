#!/usr/bin/env python3
"""Force backend + bridge pause state; write runtime_config + live_state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER

PAUSE = {"control": "pause", "reason": "sync pause — web and backend aligned"}


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)

    def run(cmd: str, timeout: int = 60) -> str:
        _, o, e = c.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    pause_json = json.dumps(PAUSE)
    run(f"printf '%s' '{pause_json}' > '{PROJ}/logs/runtime_config.json'")
    run(f"touch '{PROJ}/logs/STOP_TRADING'")

    patch = (
        "import json, pathlib; "
        f"p=pathlib.Path('{PROJ}/logs/live_state.json'); "
        "d=json.loads(p.read_text()) if p.is_file() else {}; "
        "d.update({'status':3,'kill_reason':'sync pause — web and backend aligned'}); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(json.dumps(d, ensure_ascii=False, indent=2)); "
        "print('live_state -> PAUSED')"
    )
    run(f"python3 -c {json.dumps(patch)}")

    print("=== bot process ===")
    print(run("pgrep -af 'trading-core|start_bot' || echo STOPPED"))

    print("=== bridge pause API ===")
    print(
        run(
            f"curl -s -o /dev/null -w '%{{http_code}}' -X POST "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"action\":\"pause\",\"reason\":\"sync pause via deploy\"}}' "
            f"http://127.0.0.1:8081/api/control 2>/dev/null || echo bridge_down"
        )
    )

    print("=== runtime_config ===")
    print(run(f"cat '{PROJ}/logs/runtime_config.json'"))

    c.close()
    print("\n已写入 PAUSED；若 bot 在跑，下一轮会读 runtime_config 暂停。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
