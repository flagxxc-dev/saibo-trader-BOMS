#!/usr/bin/env python3
"""One-shot bot status for server CLI (preflight file + optional live WS snapshot)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PREFLIGHT_PATH = Path(os.getenv("PREFLIGHT_PATH", "logs/preflight.json"))
WS_URL = os.getenv("BOT_WS_URL", f"ws://127.0.0.1:{os.getenv('WS_PORT', '8080')}")


def _print_preflight(path: Path) -> bool:
    if not path.is_file():
        print("自检: (无 logs/preflight.json，请先运行 python3 start_bot.py 或 live_preflight.py)")
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    mode = (report.get("mode") or "?").upper()
    ok = report.get("ok", False)
    print(f"自检: {'✅ 通过' if ok else '⚠️  有问题'}  模式={mode}  时间={report.get('ts', '?')}")
    for c in report.get("checks") or []:
        mark = "OK" if c.get("ok") else "FAIL"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  [{mark}] {c.get('name', '?')}{detail}")
    fm = report.get("fee_model") or {}
    if fm.get("sample"):
        s = fm["sample"]
        print(
            f"  费率样本: 动态≈{s.get('dynamic_fee_per_share', 0):.4f}/份 "
            f"扁平≈{s.get('flat_fee_per_share', 0):.4f}/份"
        )
    return True


async def _print_live(ws_url: str, timeout: float) -> None:
    try:
        import websockets
    except ImportError:
        print("live: websockets 未安装", file=sys.stderr)
        return

    try:
        async with websockets.connect(ws_url, open_timeout=timeout, close_timeout=2) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            d = json.loads(raw)
    except Exception as exc:
        print(f"live: 无法连接 {ws_url} ({exc})")
        return

    paper = d.get("isPaperMode", True)
    print(
        f"live: {'纸面' if paper else '实盘'}  "
        f"余额=${float(d.get('balance') or 0):.2f}  "
        f"持仓={d.get('openCount', 0)}  "
        f"状态={d.get('statusReason') or d.get('status', '?')}"
    )
    print(
        f"  5m={'开' if d.get('dhEnable5m') else '关'} "
        f"BTC={'✓' if d.get('dhEnable5mBtc', True) else '✗'} "
        f"ETH={'✓' if d.get('dhEnable5mEth', True) else '✗'} "
        f"SOL={'✓' if d.get('dhEnable5mSol', True) else '✗'}"
    )
    print(
        f"  15m={'开' if d.get('dhEnable15m') else '关'} "
        f"BTC={'✓' if d.get('dhEnable15mBtc', True) else '✗'} "
        f"ETH={'✓' if d.get('dhEnable15mEth', True) else '✗'}"
    )
    print(
        f"  动态费率={'是' if d.get('useDynamicFees') else '否'}  "
        f"扫描市场={d.get('marketsScanned', '?')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="查看 bot 自检与运行状态")
    parser.add_argument("--live", action="store_true", help="额外拉取 WS 实时快照")
    parser.add_argument("--ws", default=WS_URL, help=f"WebSocket 地址 (默认 {WS_URL})")
    parser.add_argument("--timeout", type=float, default=8.0, help="WS 超时秒数")
    args = parser.parse_args()

    _print_preflight(PREFLIGHT_PATH)
    if args.live:
        asyncio.run(_print_live(args.ws, args.timeout))
    return 0


if __name__ == "__main__":
    sys.exit(main())
