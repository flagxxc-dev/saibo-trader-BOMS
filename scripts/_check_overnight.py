#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

import paramiko

HOST = "70.34.221.132"
PROJ = "/opt/polymarket-bot"


def connect() -> paramiko.SSHClient:
    text = (Path(__file__).resolve().parents[1] / ".deploy.local").read_text(encoding="utf-8")
    pw = key = None
    for line in text.splitlines():
        if line.startswith("PASSWORD="):
            pw = line.split("=", 1)[1].strip()
        if line.startswith("KEY_PATH="):
            key = line.split("=", 1)[1].strip()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key and os.path.isfile(key):
        client.connect(HOST, username="root", key_filename=key)
    else:
        client.connect(HOST, username="root", password=pw)
    return client


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, _ = client.exec_command(cmd, timeout=60)
    return stdout.read().decode(errors="replace").strip()


def main() -> int:
    client = connect()
    try:
        print("ENV:")
        print(
            run(
                client,
                f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_LEG1_COOLDOWN|LIH_REBALANCE_COOLDOWN)' '{PROJ}/.env'",
            )
        )
        print("\nPROCESSES:")
        print(run(client, "pgrep -af 'start_bot|trading-core|next-server' | head -5"))
        print("\nSHADOW count:")
        print(run(client, f"grep -c 'LIH SHADOW' '{PROJ}/logs/bridge.log' 2>/dev/null; true"))
        print("\nRecent bridge:")
        print(run(client, f"tail -5 '{PROJ}/logs/bridge.log'"))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
