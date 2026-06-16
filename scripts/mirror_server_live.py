#!/usr/bin/env python3
"""
Forward live server bot.log book/signal lines into local paper bot mirror API.

Usage (host, while local Docker bot runs on :8081):
  python scripts/mirror_server_live.py

Env:
  SERVER_SSH_HOST      default 70.34.221.132
  LOCAL_MIRROR_URL     default http://127.0.0.1:8081/api/mirror
  MIRROR_POLL_SEC      default 5
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import paramiko
import requests

ROOT = Path(__file__).resolve().parents[1]

BOOK_RE = re.compile(
    r"Book vs WS (\w+) \| WS YES:([\d.]+) NO:([\d.]+) SUM:([\d.]+) \| "
    r"BOOK YES:([\d.]+) NO:([\d.]+) SUM:([\d.]+)"
)
DETECTED_RE = re.compile(
    r"DUMP-HEDGE DETECTED.*\| (\w+) 5m \| YES: ([\d.]+) NO: ([\d.]+) \| Sum: ([\d.]+)"
)
SHADOW_RE = re.compile(
    r"\[LIVE DH SHADOW\] WOULD OPEN \| (\w+)"
)


def ssh_password() -> str:
    p = ROOT / ".deploy.local"
    if not p.is_file():
        return ""
    m = re.search(r'DEPLOY_SSH_PASSWORD\s*=\s*["\'](.+?)["\']', p.read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def tail_remote_log(host: str, password: str, lines: int = 80) -> str:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="root", password=password, timeout=20)
    _, o, _ = c.exec_command(f"tail -n {lines} /opt/polymarket-bot/bot.log", timeout=30)
    text = o.read().decode(errors="replace")
    c.close()
    return text


def parse_assets(text: str) -> dict:
    assets: dict[str, dict] = {}
    for line in text.splitlines():
        m = BOOK_RE.search(line)
        if m:
            asset = m.group(1).lower()
            assets[asset] = {
                "ws_yes": float(m.group(2)),
                "ws_no": float(m.group(3)),
                "ws_sum": float(m.group(4)),
                "book_yes": float(m.group(5)),
                "book_no": float(m.group(6)),
                "book_sum": float(m.group(7)),
                "source": "server_book",
            }
            continue
        m = DETECTED_RE.search(line)
        if m:
            asset = m.group(1).lower()
            cur = assets.setdefault(asset, {"source": "server_detected"})
            cur.update({
                "ws_yes": float(m.group(2)),
                "ws_no": float(m.group(3)),
                "ws_sum": float(m.group(4)),
            })
            continue
        if SHADOW_RE.search(line):
            asset = SHADOW_RE.search(line).group(1).lower()
            assets.setdefault(asset, {})["shadow_would_open"] = True
    return assets


def post_mirror(url: str, assets: dict) -> None:
    if not assets:
        return
    payload = {
        "updated_at": time.time(),
        "source": "server_live",
        "assets": assets,
    }
    r = requests.post(url, json=payload, timeout=8)
    r.raise_for_status()
    print(f"[mirror] posted {len(assets)} assets -> {url}", flush=True)


def main() -> int:
    host = os.getenv("SERVER_SSH_HOST", "70.34.221.132")
    url = os.getenv("LOCAL_MIRROR_URL", "http://127.0.0.1:8081/api/mirror")
    interval = float(os.getenv("MIRROR_POLL_SEC", "5"))
    pw = ssh_password()
    if not pw:
        print("Missing .deploy.local SSH password — mirror from log disabled.", file=sys.stderr)
        return 1

    print(f"Mirroring server {host} log -> {url} every {interval}s", flush=True)
    while True:
        try:
            text = tail_remote_log(host, pw)
            assets = parse_assets(text)
            post_mirror(url, assets)
        except Exception as exc:
            print(f"[mirror] error: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
