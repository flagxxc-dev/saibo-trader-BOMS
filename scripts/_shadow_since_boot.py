#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password  # noqa: E402


def run(c, cmd):
    _, o, e = c.exec_command(cmd, timeout=90, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    log = f"{PROJ}/logs/bridge.log"
    try:
        line = run(c, f"grep -n 'CORE 就绪.*实盘.*LIH' '{log}' | tail -1")
        print("Boot line:", line)
        n = line.split(":")[0] if ":" in line else "1"
        print("\n=== Since last live LIH boot ===")
        print(run(c, f"sed -n '{n},$p' '{log}' | grep -aF '[LIVE LIH SHADOW]' | grep -v '出现' | tail -15"))
        print("\n=== dry_run count since boot ===")
        print(run(c, f"sed -n '{n},$p' '{log}' | grep -aF 'dry_run' | grep -aF 'SHADOW' | wc -l"))
        print("\n=== bot.log SHADOW ===")
        print(run(c, f"grep -a 'LIH SHADOW' '{PROJ}/logs/bot.log' 2>/dev/null | tail -10 || true"))
        print("\n=== bridge SHADOW (all) ===")
        print(run(c, f"grep -a 'LIH SHADOW' '{log}' | grep -v '出现' | tail -10"))
        print("\n=== resume / pause config ===")
        print(run(c, f"grep -a 'CONFIG RESUME\\|pause_after_round\\|Auto-pause' '{log}' | tail -8"))
        print(
            run(
                c,
                "curl -s http://127.0.0.1:8081/api/config | python3 -c "
                "'import sys,json; l=json.load(sys.stdin).get(\"live\",{}); "
                "print(\"status\", l.get(\"status\"), l.get(\"statusReason\",\"\")); "
                "print(\"openCount\", l.get(\"openCount\")); "
                "print(\"dryRun\", l.get(\"liveLihDryRun\")); "
                "print(\"pauseAfterRound\", l.get(\"lihPauseAfterRound\"))'",
            )
        )
    finally:
        c.close()


if __name__ == "__main__":
    main()
