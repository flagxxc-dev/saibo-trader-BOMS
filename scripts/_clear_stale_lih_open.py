#!/usr/bin/env python3
"""Clear stale open LIH rows from live_state and reload core memory."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402

REMOTE_CLEAR = r"""
import json
from pathlib import Path
p = Path("logs/live_state.json")
d = json.loads(p.read_text(encoding="utf-8"))
before = len(d.get("open_lih_positions") or {})
d["open_lih_positions"] = {}
d["lih_session_legs_used"] = 0
p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"cleared {before} open row(s)")
"""


def run_out(c: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    try:
        sftp = c.open_sftp()
        remote = f"{PROJ}/scripts/_tmp_clear_lih.py"
        sftp.putfo(__import__("io").BytesIO(REMOTE_CLEAR.encode()), remote)
        sftp.close()

        print(run_out(c, f"cd '{PROJ}' && .venv/bin/python scripts/_tmp_clear_lih.py"))

        for control in ("reload_lih_state", "resume"):
            body = json.dumps({"control": control, "user": "clear-stale-open"})
            run_out(c, f"printf '%s' '{body}' > '{PROJ}/logs/runtime_config.json'")
            time.sleep(4)

        print("\n=== API ===")
        print(
            run_out(
                c,
                "curl -s http://127.0.0.1:8081/api/config | python3 -c "
                "'import sys,json; l=json.load(sys.stdin).get(\"live\",{}); "
                "print(\"status\", l.get(\"status\"), l.get(\"statusReason\",\"\")); "
                "print(\"openCount\", l.get(\"openCount\")); "
                "print(\"dryRun\", l.get(\"liveLihDryRun\"))'",
            )
        )
        print("\n=== CORE ===")
        print(run_out(c, f"grep -a 'CORE 就绪' '{PROJ}/logs/bridge.log' | tail -1"))
        print("\n=== live_state file ===")
        print(
            run_out(
                c,
                f"cd '{PROJ}' && .venv/bin/python -c \"import json; d=json.load(open('logs/live_state.json')); "
                "print('open', len(d.get('open_lih_positions',{})))\"",
            )
        )
        print("\n=== reload log ===")
        print(run_out(c, f"grep -a 'reload_lih_state\\|Live LIH state restored' '{PROJ}/logs/bridge.log' | tail -5"))
        run_out(c, f"rm -f '{PROJ}/scripts/_tmp_clear_lih.py'")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
