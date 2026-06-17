#!/usr/bin/env python3
"""Resume shadow LIH: disable pause-after-round, resume trading, keep DRY_RUN."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, ROOT, USER, load_password, run  # noqa: E402


def run_out(client: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    return (stdout.read() + stderr.read()).decode(errors="replace").strip()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=load_password(), timeout=30)

    ok = True
    try:
        sftp = client.open_sftp()
        sftp.put(str(ROOT / "trading-core/src/main.cpp"), f"{PROJ}/trading-core/src/main.cpp")
        sftp.put(str(ROOT / "dashboard_bridge.py"), f"{PROJ}/dashboard_bridge.py")
        sftp.close()

        run_out(client, f"cd '{PROJ}' && ./build.sh", timeout=1800)

        env_fix = (
            f"sed -i 's/^LIH_PAUSE_AFTER_ROUND=.*/LIH_PAUSE_AFTER_ROUND=false/' '{PROJ}/.env'; "
            f"grep -q '^LIH_PAUSE_AFTER_ROUND=' '{PROJ}/.env' || "
            f"echo 'LIH_PAUSE_AFTER_ROUND=false' >> '{PROJ}/.env'; "
            f"sed -i 's/^#POLYMARKET_PRIVATE_KEY=/POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'"
        )
        run_out(client, env_fix)
        run_out(client, f"grep -E '^(LIH_PAUSE_AFTER_ROUND|PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED)' '{PROJ}/.env'")

        # Clear stale reconcile rows before restart (shadow must not inherit ghost open slots).
        remote_clear = (
            "import json; from pathlib import Path; "
            "p=Path('logs/live_state.json'); "
            "d=json.loads(p.read_text(encoding='utf-8')); "
            "d['open_lih_positions']={}; d['lih_session_legs_used']=0; "
            "p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); "
            "print('cleared open_lih')"
        )
        run_out(client, f"cd '{PROJ}' && .venv/bin/python -c \"{remote_clear}\"")

        run(client, f"bash '{PROJ}/server_start_bot.sh'", timeout=120)
        time.sleep(16)

        cfg = json.dumps(
            {
                "control": "resume",
                "reason": "shadow LEG1 sample run",
                "user": "resume-shadow",
                "patch": {"LIH_PAUSE_AFTER_ROUND": "false"},
            },
            ensure_ascii=False,
        )
        run_out(client, f"printf '%s' '{cfg}' > '{PROJ}/logs/runtime_config.json'")
        time.sleep(5)

        run_out(
            client,
            "curl -s http://127.0.0.1:8081/api/config | python3 -c "
            "'import sys,json; l=json.load(sys.stdin).get(\"live\",{}); "
            "print(\"status:\", l.get(\"status\")); "
            "print(\"reason:\", l.get(\"statusReason\",\"\")); "
            "print(\"dryRun:\", l.get(\"liveLihDryRun\")); "
            "print(\"session:\", l.get(\"lihSessionLegsUsed\"),\"/\",l.get(\"lihSessionMaxLegs\")); "
            "print(\"balance:\", l.get(\"balance\"), \"wallet:\", l.get(\"realWalletBalance\"))'",
        )

        procs = run_out(client, "pgrep -af 'start_bot|trading-core' || echo NONE")
        if "trading-core" not in procs:
            ok = False

        core = run_out(client, f"grep -a 'CORE 就绪' '{PROJ}/logs/bridge.log' | tail -1")
        if "PAUSED" in core or "round complete" in core:
            ok = False
            print("WARN: core still shows paused/round complete")

        resume_log = run_out(
            client,
            f"grep -aE 'CONFIG RESUME|lih_pause_after_round=false|Trading resumed' '{PROJ}/logs/bridge.log' | tail -5",
        )
        print(f"\nResume telemetry:\n{resume_log}")

        run_out(client, f"sed -i 's/^POLYMARKET_PRIVATE_KEY=/#POLYMARKET_PRIVATE_KEY=/' '{PROJ}/.env'")
        key = run_out(
            client,
            f"grep 'POLYMARKET_PRIVATE_KEY' '{PROJ}/.env' | sed 's/=.*/=***REDACTED***/' | head -1",
        )
        print(f"Key after: {key}")

        shadow = run_out(
            client,
            f"grep -aF 'dry_run' '{PROJ}/logs/bridge.log' | grep -aF 'SHADOW' | tail -3",
        )
        print(f"\nRecent shadow (may appear after next signal):\n{shadow or '(none yet)'}")

        print(f"\n=== RESUME SHADOW: {'PASS' if ok else 'CHECK LOGS'} ===")
        return 0 if ok else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
