#!/usr/bin/env python3
"""Remote LIH shadow/live audit — mode, duplicates, recent signals."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from remote_deploy import HOST, PROJ, load_password, run  # noqa: E402

LEG1_RE = re.compile(
    r"\[(?:LIVE LIH SHADOW|LIH (?:LIVE|SHADOW|PAPER))\].*LEG1|"
    r"LIH (?:LIVE )?LEG1\s+\w+\s+(?:YES|NO)",
    re.I,
)
REAL_ORDER_RE = re.compile(r"\[LIVE EXEC\]|LIH LIVE LEG1|register.*LIVE.*LEG1", re.I)
SHADOW_RE = re.compile(r"\[LIVE LIH SHADOW\]", re.I)
BLOCKED_RE = re.compile(r"LEG1 blocked|leg1 in-flight|slot budget exceeded", re.I)


def main() -> int:
    pw = load_password()
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=30)
    try:
        print("=" * 60)
        print("  实盘/Shadow 自检")
        print("=" * 60)

        steps = [
            ("模式 .env", f"grep -E '^(PAPER_MODE|LIVE_LIH_DRY_RUN|LIH_ENABLED|LIH_MAX_USDC|RISK_MAX|LIVE_TRADES_BASELINE|PRELIVE)' '{PROJ}/.env'"),
            ("进程", f"pgrep -af 'trading-core|start_bot' | head -5"),
            ("余额", f"cd '{PROJ}' && .venv/bin/python fetch_balance.py 2>/dev/null | tail -3"),
            ("实盘成交", f"grep -E 'LIH LIVE|LIH\\] LEG1|Bridge fill|register_lih|LEG1 buy failed|size_matched' '{PROJ}/bot.log' 2>/dev/null | grep '2026-06-16 05:0' | tail -30 || echo none"),
            ("preflight", f"cd '{PROJ}' && .venv/bin/python start_bot.py --preflight-only 2>&1 | tail -25"),
            ("prelive 24h", f"cd '{PROJ}' && .venv/bin/python prelive_lih_check.py --since-hours 24 2>&1"),
            (
                "最近 LEG1/SHADOW/LIVE",
                f"grep -E 'LIVE LIH SHADOW|LIH LIVE LEG1|LIH SHADOW\\]|\\[LIVE EXEC\\]|LEG1 blocked|slot budget' "
                f"'{PROJ}/bot.log' '{PROJ}/logs/bridge.log' 2>/dev/null | tail -40",
            ),
            (
                "LEG1 计数 24h",
                f"grep -c 'LIVE LIH SHADOW.*LEG1' '{PROJ}/bot.log' 2>/dev/null; echo shadow_leg1_bot; "
                f"grep -c 'LIH LIVE LEG1' '{PROJ}/bot.log' 2>/dev/null; echo real_leg1_bot; "
                f"grep -c '\\[LIVE EXEC\\]' '{PROJ}/bot.log' 2>/dev/null; echo live_exec_bot",
            ),
            ("live_state", f"test -f '{PROJ}/logs/live_state.json' && "
             f"python3 -c \"import json; d=json.load(open('{PROJ}/logs/live_state.json')); "
             f"print('open_lih', len(d.get('open_lih_positions',{{}})), 'inflight', len(d.get('lih_leg1_inflight',[])))\" "
             f"2>/dev/null || echo no live_state"),
            ("runtime config", f"grep LIH_MAX_USDC '{PROJ}/.env'; "
             f"grep 'slot_cap=' '{PROJ}/logs/bridge.log' 2>/dev/null | tail -1"),
            ("open lih detail", f"python3 -c \"import json; d=json.load(open('{PROJ}/logs/live_state.json')); "
             "[print(k, v.get('asset'), v.get('yes_shares'), v.get('no_shares')) "
             "for k,v in d.get('open_lih_positions',{{}}).items()]\" 2>/dev/null"),
            ("clob trades", f"cd '{PROJ}' && .venv/bin/python -c "
             "\"from clob_trades import fetch_recent_trades; "
             "t=fetch_recent_trades(limit=8); print('count', len(t)); "
             "[print(r.get('side'), r.get('size'), r.get('price'), r.get('created_at','')[:19]) for r in t[:5]]\" 2>&1"),
            ("shadow since deploy", f"grep -E '\\[LIH SHADOW\\]|\\[LIVE LIH SHADOW\\]' '{PROJ}/bot.log' | wc -l; echo shadow_lines"),
        ]
        for title, cmd in steps:
            print(f"\n--- {title} ---")
            run(client, cmd, timeout=120)

        # Download bot.log tail for duplicate analysis
        sftp = client.open_sftp()
        log_text = ""
        for path in (f"{PROJ}/bot.log", f"{PROJ}/logs/bot.log"):
            try:
                with sftp.open(path) as f:
                    raw = f.read().decode("utf-8", errors="replace")
                    if len(raw) > 200_000:
                        raw = raw[-200_000:]
                    log_text += raw
                break
            except OSError:
                continue
        sftp.close()

        print("\n--- 05:03 后日志（真单/ shadow） ---")
        for line in log_text.splitlines():
            if "2026-06-16 05:0" in line or "2026-06-16 05:1" in line:
                if any(x in line for x in ("LIH LIVE", "LIVE LIH", "LIVE EXEC", "dry_run", "LIH DEBUG", "Starting Core")):
                    print(f"  {line.strip()[:150]}")
        for line in log_text.splitlines():
            if "LIVE LIH SHADOW" in line and "LEG1" in line:
                print(f"  {line.strip()[:140]}")

        blocked = sum(1 for line in log_text.splitlines() if BLOCKED_RE.search(line))
        print(f"\n  LEG1 拦截记录: {blocked} 条")

        slot_counts: dict[str, int] = defaultdict(int)
        for line in log_text.splitlines():
            if not SHADOW_RE.search(line) or "LEG1" not in line:
                continue
            m = re.search(r"LEG1\s+(\w+)\s+(\d+)m", line)
            if m:
                slot_counts[f"{m.group(1)}|{m.group(2)}m"] += 1

        print("\n--- Shadow LEG1 按 slot 统计（全日志尾部） ---")
        if not slot_counts:
            print("  无 shadow LEG1 记录")
        else:
            dups = {k: v for k, v in slot_counts.items() if v > 1}
            for k, v in sorted(slot_counts.items(), key=lambda x: -x[1])[:10]:
                flag = " ⚠ 重复" if v > 1 else ""
                print(f"  {k}: {v} 笔{flag}")
            if dups:
                print(f"\n  ⚠ 发现 {len(dups)} 个 slot 有多笔 shadow LEG1")
            else:
                print("\n  ✅ 各 slot 最多 1 笔 shadow LEG1")

        real_hits = sum(1 for line in log_text.splitlines() if REAL_ORDER_RE.search(line))
        if real_hits:
            print(f"\n  ⚠ 日志中有 {real_hits} 条疑似真实下单记录 — 请人工确认")
        else:
            print("\n  ✅ 未发现真实 CLOB 下单记录（仍在 shadow）")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
