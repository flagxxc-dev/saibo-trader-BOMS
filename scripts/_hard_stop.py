#!/usr/bin/env python3
"""Hard stop: kill bot + build, paper env, pause persisted — NO restart, NO compile."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER

PAUSE = {
    "control": "pause",
    "reason": "hard stop — no new entries until operator approves",
}


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)

    def run(cmd: str, timeout: int = 90) -> str:
        _, o, e = c.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    pause_json = json.dumps(PAUSE)
    run(f"printf '%s' '{pause_json}' > '{PROJ}/logs/runtime_config.json'")
    run(f"touch '{PROJ}/logs/STOP_TRADING'")

    env_updates = {
        "PAPER_MODE": "true",
        "LIVE_LIH_DRY_RUN": "true",
        "RISK_MAX_CONCURRENT_POSITIONS": "0",
        "LIH_ENABLED": "false",
    }
    for key, val in env_updates.items():
        run(
            f"grep -q '^{key}=' '{PROJ}/.env' && "
            f"sed -i 's/^{key}=.*/{key}={val}/' '{PROJ}/.env' || "
            f"echo '{key}={val}' >> '{PROJ}/.env'"
        )

    patch_state = (
        "import json, pathlib; "
        f"p = pathlib.Path('{PROJ}/logs/live_state.json'); "
        "d = json.loads(p.read_text()) if p.is_file() else None; "
        "print('no live_state') if d is None else "
        "(d.update({'status': 3, 'kill_reason': 'hard stop'}), "
        "p.write_text(json.dumps(d, ensure_ascii=False, indent=2)), "
        "print('live_state -> PAUSED'))"
    )
    run(f"python3 -c {shlex.quote(patch_state)}")

    kill_cmds = [
        "pkill -9 -f build.sh 2>/dev/null || true",
        "pkill -9 -f 'cmake --build' 2>/dev/null || true",
        "pkill -9 -f ninja 2>/dev/null || true",
        "pkill -9 -f cc1plus 2>/dev/null || true",
        "pkill -9 -f trading-core 2>/dev/null || true",
        "pkill -9 -f start_bot.py 2>/dev/null || true",
        "pkill -9 -f dashboard_bridge.py 2>/dev/null || true",
        "sleep 2",
        "pkill -9 -f trading-core 2>/dev/null || true",
        "pkill -9 -f start_bot.py 2>/dev/null || true",
        "sleep 1",
    ]
    for cmd in kill_cmds:
        run(cmd)

    print("=== processes ===")
    print(run("pgrep -af 'build|ninja|trading-core|start_bot|cc1plus' || echo ALL_STOPPED"))

    print("=== env ===")
    print(run(f"grep -E '^PAPER_MODE=|^LIVE_LIH|^RISK_MAX_CONCURRENT|^LIH_ENABLED=' '{PROJ}/.env'"))

    print("=== runtime_config ===")
    print(run(f"cat '{PROJ}/logs/runtime_config.json' 2>/dev/null || echo missing"))

    print("=== recent leg1 / pause (bot.log tail) ===")
    print(run(f"tail -80 '{PROJ}/bot.log' 2>/dev/null | grep -E 'LEG1|PAUSED|RESUME|entry-wait|CLOB|order' | tail -25 || echo no log"))

    print("=== recent bridge ===")
    print(run(f"tail -30 '{PROJ}/logs/bridge.log' 2>/dev/null || echo no bridge log"))

    c.close()
    print("\n已硬停：进程已杀、LIH 已关、纸面模式、未编译、未启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
