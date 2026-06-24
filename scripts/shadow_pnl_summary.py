#!/usr/bin/env python3
"""Summarize shadow PnL: C++ LIH (shadow_lih_pnl.csv) and/or dual A/B CSVs."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def summarize_rows(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"{label}: (no rounds)")
        return
    pnls = [float(r["pnl_usdc"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    combs = sorted(
        float(r["combined_avg"]) for r in rows if float(r.get("matched") or 0) > 0
    )
    med = combs[len(combs) // 2] if combs else 0.0
    feed = rows[-1].get("feed_mode") or rows[-1].get("phase") or ""
    extra = f" feed={feed}" if feed else ""
    print(
        f"{label}: rounds={len(rows)} W/L={wins}/{len(rows)-wins} "
        f"PnL=${sum(pnls):+.2f} med_comb={med:.3f}{extra}"
    )


def load_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def summarize_lih() -> int:
    path = LOG_DIR / "shadow_lih_pnl.csv"
    rows = load_csv(path)
    if not rows:
        print(f"No LIH shadow rounds: {path}")
        return 0
    pnls = [float(r["pnl_usdc"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    print(f"=== LIH shadow ({path.name}) ===")
    print(f"Rounds: {len(rows)} | W/L: {wins}/{len(rows)-wins}")
    print(f"Total: ${sum(pnls):+.2f} | Median: ${sorted(pnls)[len(pnls)//2]:+.2f}")
    if rows[-1].get("cum_pnl"):
        print(f"Cum: ${float(rows[-1]['cum_pnl']):+.2f}")
    print("Last 3:")
    for r in rows[-3:]:
        asset = r.get("asset", "?")
        win = r.get("window_min", "?")
        print(
            f"  {asset} {win}m comb={float(r['combined_avg']):.4f} "
            f"PnL ${float(r['pnl_usdc']):+.2f}"
        )
    return 0


def summarize_dual(phase: int | None = None) -> int:
    for name, fname in (("A cheap_sweep", "shadow_dual_a.csv"), ("B mm_dca", "shadow_dual_b.csv")):
        rows = load_csv(LOG_DIR / fname)
        if phase is not None:
            rows = [r for r in rows if str(r.get("phase", "")) == str(phase)]
        summarize_rows(rows, name)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow PnL summary")
    parser.add_argument("--dual", action="store_true", help="Dual strategy A/B CSVs")
    parser.add_argument("--lih", action="store_true", help="C++ LIH shadow CSV")
    parser.add_argument("--phase", type=int, default=None, help="Filter dual CSV by phase")
    parser.add_argument("--compare", action="store_true", help="Run dual phase compare report")
    args = parser.parse_args()

    if args.compare:
        sys.path.insert(0, str(ROOT / "scripts"))
        from shadow_dual_strategies import compare_and_report, load_phase_marker

        marker = load_phase_marker()
        if not marker:
            print("No phase marker: logs/shadow_dual_phase_marker.json")
            return 1
        print(compare_and_report(marker))
        return 0

    if not args.dual and not args.lih:
        args.dual = args.lih = True

    if args.lih:
        summarize_lih()
        print()
    if args.dual:
        print("=== Dual shadow ===")
        summarize_dual(args.phase)
    return 0


if __name__ == "__main__":
    sys.exit(main())
