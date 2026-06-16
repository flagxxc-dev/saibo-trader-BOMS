#!/usr/bin/env python3
"""Analyze missed vs opened opportunities from server bot.log."""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
PROJ = "/opt/polymarket-bot"
LIVE_START = "2026-06-12 20:17"
NEW_CODE = "2026-06-13 05:35"  # trading-core rebuild + depth resize


@dataclass
class Event:
    ts: str
    kind: str
    asset: str
    detail: str
    sum_p: float = 0.0
    locked: float = 0.0


def pw() -> str:
    return re.search(
        r'DEPLOY_SSH_PASSWORD\s*=\s*["\'](.+?)["\']',
        (ROOT / ".deploy.local").read_text(encoding="utf-8"),
    ).group(1)


def parse(text: str) -> list[Event]:
    events: list[Event] = []
    det = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*DUMP-HEDGE DETECTED.*\| (\w+) 5m \| "
        r"YES: [\d.]+ NO: [\d.]+ \| Sum: ([\d.]+) \| Locked: ([\d.]+)/share"
    )
    for m in det.finditer(text):
        events.append(Event(m.group(1), "signal", m.group(2), "", float(m.group(3)), float(m.group(4))))

    for pat, kind in [
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*Insufficient book depth for (\w+)", "depth_abort"),
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*Depth resize (\w+) \|", "depth_resize"),
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*\[LIVE DH\] OPENED \| (\w+)", "opened"),
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*YES leg failed for (\w+)", "yes_fail"),
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*below MIN_ORDER", "min_order"),
        (r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*invalid signature", "sig_fail"),
    ]:
        rx = re.compile(pat)
        for m in rx.finditer(text):
            events.append(Event(m.group(1), kind, m.group(2) if m.lastindex >= 2 else "", m.group(0)[-80:]))
    events.sort(key=lambda e: e.ts)
    return events


def in_range(ts: str, start: str) -> bool:
    return ts >= start


def main() -> None:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("70.34.221.132", username="root", password=pw(), timeout=30)
    _, o, _ = c.exec_command(f"cat {PROJ}/bot.log", timeout=180)
    text = o.read().decode(errors="replace")
    c.close()

    all_ev = parse(text)
    live = [e for e in all_ev if in_range(e.ts, LIVE_START)]
    after_new = [e for e in all_ev if in_range(e.ts, NEW_CODE)]

    signals = [e for e in live if e.kind == "signal"]
    opened = [e for e in live if e.kind == "opened"]
    depth = [e for e in live if e.kind == "depth_abort"]
    resize = [e for e in live if e.kind == "depth_resize"]
    other_fail = [e for e in live if e.kind in ("yes_fail", "min_order", "sig_fail")]

    sig_after = [e for e in after_new if e.kind == "signal"]
    opened_after = [e for e in after_new if e.kind == "opened"]

    print("=" * 60)
    print("  实盘机会统计（LIVE 自 6/12 20:17 起）")
    print("=" * 60)
    print(f"  DH 信号（检测到折价）     : {len(signals)} 次")
    print(f"  深度不足 abort           : {len(depth)} 次")
    print(f"  深度缩小后仍尝试         : {len(resize)} 次")
    print(f"  真实 OPENED（上车）      : {len(opened)} 次")
    print(f"  其他失败（腿/签名/MIN）  : {len(other_fail)} 次")
    print()
    print(f"  → 有信号没上车（约）     : {len(signals) - len(opened)} 次")
    print(f"     （其中绝大多数是 depth abort）")

    print("\n" + "-" * 60)
    print("  新代码上线后（6/13 05:35，含 Depth resize）")
    print("-" * 60)
    print(f"  信号: {len(sig_after)}  |  OPENED: {len(opened_after)}  |  resize: {len([e for e in after_new if e.kind=='depth_resize'])}  |  depth abort: {len([e for e in after_new if e.kind=='depth_abort'])}")

    by_asset = Counter(e.asset for e in signals)
    depth_asset = Counter(e.asset for e in depth)
    print("\n  信号按币种:", dict(by_asset))
    print("  depth abort 按币种:", dict(depth_asset))

    # Best missed (signals with no opened nearby - simplified: top signals by locked)
    missed = sorted(signals, key=lambda s: (-s.locked, s.sum_p))[:12]
    print("\n" + "=" * 60)
    print("  折价最好 TOP 12 信号（是否 OPENED 见上：目前全是 0）")
    print("=" * 60)
    opened_ts = {e.ts[:19] + e.asset for e in opened}
    for i, s in enumerate(missed, 1):
        tag = "✓已开" if (s.ts[:19] + s.asset) in opened_ts else "✗未上"
        print(
            f"  {i:2}. [{tag}] {s.ts} {s.asset.upper():3} "
            f"Sum={s.sum_p:.3f} locked≈${s.locked:.3f}/份 折价≈{(1-s.sum_p)*100:.0f}%"
        )

    if resize:
        print("\n  Depth resize 记录（新逻辑）:")
        for e in resize[-8:]:
            print(f"    {e.ts} {e.asset} …")

    recent = [e for e in live if e.ts >= "2026-06-13 05:35"]
    if recent:
        print("\n  新代码后最近事件:")
        for e in recent[-15:]:
            if e.kind != "signal":
                print(f"    {e.ts} [{e.kind}] {e.asset}")


if __name__ == "__main__":
    main()
