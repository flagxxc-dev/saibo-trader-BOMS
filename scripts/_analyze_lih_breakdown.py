#!/usr/bin/env python3
"""Break down LIH wins/losses by combined avg, size, asset."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def comb(p: dict) -> float | None:
    ys, ns = p["yes_shares"], p["no_shares"]
    if ys <= 0 or ns <= 0:
        return None
    return p["yes_cost"] / ys + p["no_cost"] / ns


def matched(p: dict) -> float:
    return min(p["yes_shares"], p["no_shares"])


def main() -> None:
    d = json.loads((ROOT / "logs/paper_state.json").read_text(encoding="utf-8"))
    closed = d["closed_lih_positions"]

    wins, losses = [], []
    small, big = [], []
    for p in closed:
        m = matched(p)
        c = comb(p)
        pnl = p["pnl_usdc"]
        rec = {
            "asset": p["asset"],
            "m": m,
            "c": c,
            "pnl": pnl,
            "rebal": p.get("rebalance_count", 0),
        }
        if pnl > 0:
            wins.append(rec)
        else:
            losses.append(rec)
        (big if m > 20 else small).append(rec)

    print("=== account ===")
    print(
        f"start ${d['starting_balance']:.2f} -> now ${d['current_balance']:.2f} | "
        f"lih_pnl ${d['lih_pnl']:.2f} | peak ${d['peak_balance']:.2f}"
    )
    opened = [p["opened_at"] for p in closed]
    print(
        f"period: {datetime.fromtimestamp(min(opened), tz=timezone.utc)} "
        f"-> {datetime.fromtimestamp(max(opened), tz=timezone.utc)}"
    )

    print("\n=== PnL by matched size ===")
    for name, arr in [("small (<=20sh)", small), ("big (>20sh)", big)]:
        pnls = [x["pnl"] for x in arr]
        print(f"  {name}: n={len(arr)} total={sum(pnls):+.2f} avg={statistics.mean(pnls):+.3f}")

    print("\n=== Loss breakdown (why lost) ===")
    loss_by_comb: dict[str, list[float]] = defaultdict(list)
    for x in losses:
        c = x["c"]
        if c is None:
            bucket = "unknown"
        elif c >= 1.02:
            bucket = "comb>=1.02 expensive flex-hedge"
        elif c >= 1.0:
            bucket = "comb 1.00-1.02 over 1 + fees"
        elif c >= 0.98:
            bucket = "comb 0.98-1.00 fees ate edge"
        else:
            bucket = "comb<0.98 other"
        loss_by_comb[bucket].append(x["pnl"])
    for k, v in sorted(loss_by_comb.items(), key=lambda kv: sum(kv[1])):
        print(f"  {k}: {len(v)} rounds, total {sum(v):+.2f}")

    print("\n=== Win profile ===")
    win_comb = [x["c"] for x in wins if x["c"]]
    print(f"  n={len(wins)} total={sum(x['pnl'] for x in wins):+.2f}")
    print(f"  avg comb={statistics.mean(win_comb):.4f}, avg matched={statistics.mean([x['m'] for x in wins]):.1f}sh")
    print(f"  rebal=1: {sum(1 for x in wins if x['rebal']==1)}, rebal=2: {sum(1 for x in wins if x['rebal']==2)}")

    print("\n=== Loss profile ===")
    loss_comb = [x["c"] for x in losses if x["c"]]
    print(f"  n={len(losses)} total={sum(x['pnl'] for x in losses):+.2f}")
    print(f"  avg comb={statistics.mean(loss_comb):.4f}, avg matched={statistics.mean([x['m'] for x in losses]):.1f}sh")
    print(f"  rebal=1: {sum(1 for x in losses if x['rebal']==1)}, rebal=2: {sum(1 for x in losses if x['rebal']==2)}")

    print("\n=== by asset ===")
    by_asset: dict[str, list[float]] = defaultdict(list)
    for p in closed:
        by_asset[p["asset"]].append(p["pnl_usdc"])
    for a in sorted(by_asset):
        arr = by_asset[a]
        w = sum(1 for x in arr if x > 0)
        print(f"  {a}: {len(arr)} rounds W{w} pnl={sum(arr):+.2f} avg={statistics.mean(arr):+.3f}")


if __name__ == "__main__":
    main()
