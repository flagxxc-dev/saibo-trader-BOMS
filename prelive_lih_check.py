#!/usr/bin/env python3
"""Pre-live LIH safety checks — scan logs for duplicate LEG1, slot spend, env gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# LEG1 lines from shadow, live router, or risk register
LEG1_PATTERNS = [
    re.compile(
        r"\[LIVE LIH SHADOW\] LEG1\s+(\w+)\s+(\d+)m\s*\|"
        r".*?(\d+\.?\d*)sh\s+@\s+(\d+\.?\d*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"LIH LIVE LEG1\s+(\w+)\s+(?:YES|NO)\s+(\d+\.?\d*)sh\s+@\s+(\d+\.?\d*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\[LIH LIVE\] LEG1\s+(\S+)\s*\|"
        r".*?(\d+\.?\d*)sh\s+@\s+(\d+\.?\d*).*?cost\s+\$([\d.]+)",
        re.IGNORECASE,
    ),
]

TS_PATTERNS = [
    re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"),
    re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\]"),
]

BLOCKED_PATTERNS = [
    re.compile(r"LEG1 blocked — in-flight or open", re.IGNORECASE),
    re.compile(r"leg1 in-flight", re.IGNORECASE),
    re.compile(r"slot budget exceeded", re.IGNORECASE),
]

DEFAULT_LOGS = ("logs/bot.log", "logs/bridge.log", "bot.log")


@dataclass
class Leg1Event:
    asset: str
    window_m: int
    shares: float
    price: float
    cost_usdc: float
    line_no: int
    source: str
    raw: str


@dataclass
class CheckReport:
    ok: bool = True
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_slots: list[dict] = field(default_factory=list)
    slot_spend: dict[str, float] = field(default_factory=dict)
    leg1_count: int = 0
    blocked_count: int = 0

    def add_check(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": passed, "detail": detail})
        if not passed:
            self.ok = False


def _parse_ts(line: str) -> float | None:
    for pat in TS_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        raw = m.group(1).replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return None


def _parse_leg1(line: str, line_no: int, source: str) -> Leg1Event | None:
    for pat in LEG1_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        groups = m.groups()
        if len(groups) == 4 and pat.pattern.startswith(r"\[LIVE LIH SHADOW\]"):
            asset, window, shares, price = groups
            sh, px = float(shares), float(price)
            return Leg1Event(asset, int(window), sh, px, sh * px, line_no, source, line.strip())
        if len(groups) == 4 and "cost" in pat.pattern:
            _lih_id, shares, price, cost = groups
            asset_m = re.search(r"LIH-(\w+)-", line)
            asset = asset_m.group(1) if asset_m else "?"
            win_m = re.search(r"(\d+)m", line)
            window = int(win_m.group(1)) if win_m else 5
            return Leg1Event(
                asset, window, float(shares), float(price), float(cost), line_no, source, line.strip()
            )
        if len(groups) == 3:
            asset, shares, price = groups
            sh, px = float(shares), float(price)
            win_m = re.search(r"(\d+)m", line)
            window = int(win_m.group(1)) if win_m else 5
            return Leg1Event(asset, window, sh, px, sh * px, line_no, source, line.strip())
    return None


def scan_logs(
    paths: list[Path],
    window_sec: float = 300.0,
    since_hours: float = 0.0,
    since_baseline_ts: float = 0.0,
) -> CheckReport:
    report = CheckReport()
    events: list[tuple[float | None, Leg1Event]] = []
    cutoff_ts: float | None = None
    if since_baseline_ts > 0:
        cutoff_ts = since_baseline_ts
    elif since_hours > 0:
        cutoff_ts = datetime.now(timezone.utc).timestamp() - since_hours * 3600.0

    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            ts = _parse_ts(line)
            if cutoff_ts is not None and ts is not None and ts < cutoff_ts:
                continue
            if any(p.search(line) for p in BLOCKED_PATTERNS):
                report.blocked_count += 1
            ev = _parse_leg1(line, i, str(path))
            if ev:
                report.leg1_count += 1
                events.append((ts, ev))

    # Duplicate = >1 LEG1 for same asset within same market time bucket (not across 5m rounds)
    by_round: dict[str, list[Leg1Event]] = defaultdict(list)
    for ts, ev in events:
        bucket = int(ts or 0) // max(int(ev.window_m) * 60, 60)
        key = f"{ev.asset}|{ev.window_m}m|{bucket}"
        by_round[key].append(ev)
        slot_key = f"{ev.asset}|{ev.window_m}m"
        report.slot_spend[slot_key] = report.slot_spend.get(slot_key, 0.0) + ev.cost_usdc

    for key, evs in by_round.items():
        if len(evs) <= 1:
            continue
        report.duplicate_slots.append(
            {
                "slot": key,
                "count": len(evs),
                "total_usdc": round(sum(e.cost_usdc for e in evs), 2),
                "samples": [e.raw[:120] for e in evs[:3]],
            }
        )

    if report.duplicate_slots:
        worst = max(report.duplicate_slots, key=lambda x: x["count"])
        report.add_check(
            "no_duplicate_leg1_per_slot",
            False,
            f"发现 {len(report.duplicate_slots)} 个 slot 重复 LEG1；最严重 {worst['slot']} "
            f"{worst['count']} 笔 / ${worst['total_usdc']:.2f}",
        )
    else:
        report.add_check(
            "no_duplicate_leg1_per_slot",
            True,
            f"扫描 {report.leg1_count} 条 LEG1，无同 slot 重复",
        )

    if report.blocked_count > 0:
        report.warnings.append(f"日志中有 {report.blocked_count} 条 LEG1/in-flight 拦截记录（正常防护）")

    return report


def check_env(require_shadow: bool = True) -> CheckReport:
    load_dotenv()
    report = CheckReport()

    paper = os.getenv("PAPER_MODE", "true").strip().lower() in ("false", "0", "no", "off")
    lih = os.getenv("LIH_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")
    live_dry = os.getenv("LIVE_LIH_DRY_RUN", "true").strip().lower() not in ("false", "0", "no", "off")

    if not paper and lih:
        if require_shadow:
            report.add_check(
                "LIVE_LIH_DRY_RUN",
                live_dry,
                "shadow 开" if live_dry else "⚠ 将发真实订单 — 需先通过 shadow 观察",
            )
        else:
            report.add_check("LIVE_LIH_DRY_RUN", True, "真下单模式（已跳过 shadow 强制）")

        frac = float(os.getenv("RISK_MAX_POSITION_FRACTION", "0.08") or "0.08")
        slot_cap = float(os.getenv("LIH_MAX_USDC_PER_SLOT", "0") or "0")
        report.add_check(
            "slot_budget_configured",
            True,
            f"单笔比例={frac:.0%} | 局累计上限="
            + (f"${slot_cap:.2f}" if slot_cap > 0 else "余额×比例（默认）"),
        )

        leg1_max = float(os.getenv("LIH_LEG1_MAX_PRICE", "0.45") or "0.45")
        leg1_sh = float(os.getenv("LIH_LEG1_SHARES", "10") or "10")
        target = float(os.getenv("LIH_TARGET_COMBINED", "0.95") or "0.95")
        min_bal = float(os.getenv("LIH_MIN_BALANCE_USDC", "10") or "10")
        leg1_min_secs = float(os.getenv("LIH_LEG1_MIN_SECONDS_REMAINING", "30") or "30")
        hedge_min_secs = float(os.getenv("LIH_MIN_SECONDS_REMAINING", "15") or "15")
        est_round = leg1_sh * target
        report.add_check(
            "lih_min_balance",
            min_bal <= 0 or min_bal + 1e-6 >= est_round,
            f"leg1≤{leg1_max} × {leg1_sh:.0f}sh | 整局≈${est_round:.2f} | LIH_MIN_BALANCE_USDC=${min_bal:.2f}",
        )
        report.add_check(
            "lih_leg1_late_window",
            leg1_min_secs >= 15.0,
            f"末段不开仓 leg1_min={leg1_min_secs:.0f}s | 对冲底线 hedge_min={hedge_min_secs:.0f}s",
        )

        leg1_cd = float(os.getenv("LIH_LEG1_COOLDOWN_SECONDS", "20") or "20")
        report.add_check(
            "leg1_cooldown",
            leg1_cd >= 5.0,
            f"LIH_LEG1_COOLDOWN_SECONDS={leg1_cd:.0f}",
        )

        persist = os.getenv("LIVE_STATE_PERSIST", "true").strip().lower() not in (
            "false", "0", "no", "off",
        )
        report.add_check(
            "live_state_persist",
            persist,
            "LIVE_STATE_PERSIST=on" if persist else "⚠ 重启会丢 LIH 持仓记忆",
        )
    else:
        report.add_check("mode", True, "纸面模式 — 环境门禁跳过")

    return report


def run_prelive_check(
    log_paths: list[Path] | None = None,
    *,
    require_shadow: bool = True,
    min_shadow_leg1: int = 0,
    since_hours: float = 0.0,
    since_baseline: bool = False,
) -> dict:
    load_dotenv()
    baseline_ts = 0.0
    if since_baseline:
        raw = os.getenv("LIVE_TRADES_BASELINE_TS", "").strip()
        if raw:
            try:
                baseline_ts = float(raw)
            except ValueError:
                pass

    env_report = check_env(require_shadow=require_shadow)
    paths = log_paths or [Path(p) for p in DEFAULT_LOGS]
    log_report = scan_logs(
        paths,
        since_hours=since_hours if not since_baseline else 0.0,
        since_baseline_ts=baseline_ts,
    )

    merged = CheckReport()
    merged.ok = env_report.ok and log_report.ok
    merged.checks = env_report.checks + log_report.checks
    merged.warnings = env_report.warnings + log_report.warnings
    merged.duplicate_slots = log_report.duplicate_slots
    merged.slot_spend = log_report.slot_spend
    merged.leg1_count = log_report.leg1_count
    merged.blocked_count = log_report.blocked_count

    if min_shadow_leg1 > 0 and log_report.leg1_count < min_shadow_leg1:
        merged.add_check(
            "min_shadow_samples",
            False,
            f"LEG1 样本 {log_report.leg1_count} < 要求 {min_shadow_leg1}（shadow 观察不足）",
        )
    elif min_shadow_leg1 > 0:
        merged.add_check(
            "min_shadow_samples",
            True,
            f"LEG1 样本 {log_report.leg1_count} ≥ {min_shadow_leg1}",
        )

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": merged.ok,
        "checks": merged.checks,
        "warnings": merged.warnings,
        "leg1_count": merged.leg1_count,
        "blocked_count": merged.blocked_count,
        "duplicate_slots": merged.duplicate_slots,
        "slot_spend": merged.slot_spend,
        "logs_scanned": [str(p) for p in paths if p.is_file()],
        "since_baseline_ts": baseline_ts if since_baseline else None,
    }


def print_report(report: dict) -> None:
    print("=" * 60)
    print("  LIH 上线前安全检查")
    print("=" * 60)
    for c in report.get("checks", []):
        mark = "OK" if c["ok"] else "FAIL"
        line = f"  [{mark}] {c['name']}"
        if c.get("detail"):
            line += f" — {c['detail']}"
        print(line)
    if report.get("warnings"):
        print("\n[提示]")
        for w in report["warnings"]:
            print(f"  • {w}")
    if report.get("duplicate_slots"):
        print("\n[重复 LEG1 slot]")
        for d in report["duplicate_slots"][:5]:
            print(f"  • {d['slot']}: {d['count']} 笔, ${d['total_usdc']:.2f}")
    if report.get("slot_spend"):
        top = sorted(report["slot_spend"].items(), key=lambda x: -x[1])[:5]
        if top:
            print("\n[slot 累计花费 TOP]")
            for k, v in top:
                print(f"  • {k}: ${v:.2f}")
    print("\n" + ("✅ 通过 — 可考虑真下单" if report["ok"] else "⚠️  未通过 — 不要开 LIVE_LIH_DRY_RUN=false") + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="LIH 上线前日志与环境安全检查")
    parser.add_argument("--log", action="append", help="额外日志路径（可多次指定）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="不强制 LIVE_LIH_DRY_RUN=true（真下单前自检用）",
    )
    parser.add_argument(
        "--min-shadow-leg1",
        type=int,
        default=0,
        help="要求日志中至少 N 条 LEG1 shadow/模拟样本",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=float(os.getenv("PRELIVE_LOG_HOURS", "0") or "0"),
        help="只扫描最近 N 小时日志（避免旧事故误报）",
    )
    parser.add_argument(
        "--since-baseline",
        action="store_true",
        help="只扫描 LIVE_TRADES_BASELINE_TS 之后的日志",
    )
    parser.add_argument(
        "--out",
        default="logs/prelive_lih.json",
        help="JSON 报告输出路径",
    )
    args = parser.parse_args()

    paths = [Path(p) for p in DEFAULT_LOGS]
    if args.log:
        paths.extend(Path(p) for p in args.log)

    report = run_prelive_check(
        paths,
        require_shadow=not args.allow_live,
        min_shadow_leg1=args.min_shadow_leg1,
        since_hours=args.since_hours,
        since_baseline=args.since_baseline,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
        print(f"报告已写入 {out}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
