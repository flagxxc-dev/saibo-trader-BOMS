#!/usr/bin/env python3
"""One-off: compare filtered bot API vs raw Polymarket Data API trade counts."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = "70.34.221.132"
USER = "root"
PROJ = "/opt/polymarket-bot"


def connect() -> paramiko.SSHClient:
    text = (ROOT / ".deploy.local").read_text(encoding="utf-8")
    pw = key = None
    for line in text.splitlines():
        if line.startswith("PASSWORD="):
            pw = line.split("=", 1)[1].strip()
        if line.startswith("KEY_PATH="):
            key = line.split("=", 1)[1].strip()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key and os.path.isfile(key):
        client.connect(HOST, username=USER, key_filename=key)
    else:
        client.connect(HOST, username=USER, password=pw)
    return client


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=60)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if err.strip():
        print(err.strip(), file=sys.stderr)
    return out.strip()


def main() -> int:
    client = connect()
    try:
        baseline = run(client, f"grep '^LIVE_TRADES_BASELINE_TS=' '{PROJ}/.env'")
        funder_line = run(client, f"grep '^POLYMARKET_FUNDER=' '{PROJ}/.env' | head -1")
        funder = funder_line.split("=", 1)[1].strip() if "=" in funder_line else ""
        masked = f"{funder[:6]}...{funder[-4:]}" if len(funder) > 12 else funder

        filtered_raw = run(
            client,
            "curl -s 'http://127.0.0.1:8081/api/clob/trades?limit=200'",
        )
        filtered = json.loads(filtered_raw)
        filtered_count = filtered.get("count", 0)

        user = funder if funder.startswith("0x") else f"0x{funder}"
        params = urllib.parse.urlencode(
            {
                "limit": 200,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "user": user,
            }
        )
        url = f"https://data-api.polymarket.com/activity?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot/check"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        arr = raw if isinstance(raw, list) else (raw.get("data") or [])
        all_trades = [a for a in arr if isinstance(a, dict) and a.get("type") == "TRADE"]

        print("funder:", masked)
        print(baseline)
        print("bot_api_filtered_count:", filtered_count)
        print("polymarket_raw_trade_count:", len(all_trades))
        if all_trades:
            t0 = all_trades[0]
            print(
                "latest_trade:",
                (t0.get("title") or "")[:60],
                "| side=", t0.get("side"),
                "| size=", t0.get("size"),
                "| ts=", t0.get("timestamp"),
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
