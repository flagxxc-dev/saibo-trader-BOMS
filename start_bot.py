#!/usr/bin/env python3
"""Server CLI entry: preflight + config summary + dashboard_bridge (with terminal output)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from live_preflight import PREFLIGHT_PATH, print_report, run_preflight


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("false", "0", "no", "off"):
        return False
    if raw in ("true", "1", "yes", "on"):
        return True
    return default


def _enabled_assets(prefix: str, assets: tuple[str, ...]) -> str:
    on = [a.upper() for a in assets if _env_bool(f"{prefix}_{a.upper()}", True)]
    return " · ".join(on) if on else "全关"


def print_config_summary() -> None:
    load_dotenv()
    paper = _env_bool("PAPER_MODE", True)
    mode = "纸面 PAPER" if paper else "实盘 LIVE"

    print("=" * 60)
    print(f"  运行配置摘要  |  {mode}")
    print("=" * 60)
    print(f"  WS   : ws://{os.getenv('WS_HOST', '0.0.0.0')}:{os.getenv('WS_PORT', '8080')}")
    print(f"  API  : http://{os.getenv('WS_HOST', '0.0.0.0')}:{os.getenv('HTTP_PORT', '8081')}")
    print(f"  5m   : {'开' if _env_bool('DH_ENABLE_5M') else '关'}  →  {_enabled_assets('DH_ENABLE_5M', ('btc', 'eth', 'sol'))}")
    print(f"  15m  : {'开' if _env_bool('DH_ENABLE_15M') else '关'}  →  {_enabled_assets('DH_ENABLE_15M', ('btc', 'eth'))}")
    print(
        f"  DH   : sum≤{os.getenv('DH_SUM_TARGET', '0.95')}  "
        f"disc≥{os.getenv('DH_MIN_DISCOUNT', '0.03')}  "
        f"cd={os.getenv('DH_COOLDOWN_SECONDS', '30')}s"
    )
    print(
        f"  风控 : pos={os.getenv('RISK_MAX_POSITION_FRACTION', '0.08')}  "
        f"daily={os.getenv('RISK_DAILY_LOSS_LIMIT', '0.20')}  "
        f"max_pos={os.getenv('RISK_MAX_CONCURRENT_POSITIONS', '3')}"
    )
    if not paper:
        funder = os.getenv("POLYMARKET_FUNDER", "")
        signer = os.getenv("POLYMARKET_SIGNER", funder)
        print(f"  钱包 : funder={funder or '(未设置)'}  signer={signer or '(未设置)'}")
    print("=" * 60 + "\n")


def run_preflight_step() -> dict:
    report = run_preflight()
    PREFLIGHT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFLIGHT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 bot（自检 + 配置摘要 + bridge/core）")
    parser.add_argument("--skip-preflight", action="store_true", help="跳过自检（Docker entrypoint 已跑过时）")
    parser.add_argument("--preflight-only", action="store_true", help="只跑自检并退出")
    parser.add_argument("--json-only", action="store_true", help="只输出自检 JSON（供脚本用）")
    args = parser.parse_args()

    if args.preflight_only or args.json_only:
        report = run_preflight()
        PREFLIGHT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFLIGHT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json_only:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print_report(report)
        if not report.get("paper_mode") and not report.get("ok"):
            return 1
        return 0

    if not args.skip_preflight:
        report = run_preflight_step()
        if not report.get("paper_mode") and not report.get("ok"):
            print("❌ 实盘自检未通过，已中止启动。", file=sys.stderr)
            return 1
    else:
        if PREFLIGHT_PATH.is_file():
            try:
                report = json.loads(PREFLIGHT_PATH.read_text(encoding="utf-8"))
                ok = report.get("ok", True)
                mode = (report.get("mode") or "paper").upper()
                print(f"[preflight] 使用已有报告 ({mode}) ok={ok}", file=sys.stderr)
            except Exception:
                pass

    print_config_summary()
    os.environ["PREFLIGHT_SKIP"] = "1"

    print("[启动] dashboard_bridge + trading-core …", file=sys.stderr)
    print("       Ctrl+C 停止 · 日志另见 logs/bot.log\n", file=sys.stderr)

    from dashboard_bridge import main as bridge_main

    try:
        asyncio.run(bridge_main())
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
