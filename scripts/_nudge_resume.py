#!/usr/bin/env python3
"""Ensure shadow bot is ACTIVE after resume fix."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402


def run(c, cmd, t=90):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    try:
        body = json.dumps(
            {
                "control": "resume",
                "reason": "shadow LEG1 sampling",
                "user": "resume-shadow",
                "patch": {"LIH_PAUSE_AFTER_ROUND": "false"},
            }
        )
        run(c, f"printf '%s' '{body}' > '{PROJ}/logs/runtime_config.json'")
        time.sleep(5)
        print(run(c, "curl -s http://127.0.0.1:8081/api/config | python3 -c "
              "'import sys,json; l=json.load(sys.stdin).get(\"live\",{}); "
              "print(\"status\", l.get(\"status\"), l.get(\"statusReason\",\"\")); "
              "print(\"openCount\", l.get(\"openCount\")); "
              "print(\"dryRun\", l.get(\"liveLihDryRun\"))'"))
        print(run(c, f"grep -a 'CORE 就绪' '{PROJ}/logs/bridge.log' | tail -1"))
    finally:
        c.close()


if __name__ == "__main__":
    main()
