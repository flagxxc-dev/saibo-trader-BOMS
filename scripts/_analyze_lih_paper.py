#!/usr/bin/env python3
"""Summarize LIH paper trading from logs/paper_state.json."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def combined_avg(p: dict) -> float | None:
    ys, ns = p.get("yes_shares", 0.0), p.get("no_shares", 0.0)
    if ys <= 0 or ns <= 0:
        return None
    return p["yes_cost"] / ys + p["no_cost"] / ns


def matched_shares(p: dict) -> float:
    return min(p.get("yes_shares", 0.0), p.get("no_shares", 0.0))


def gap_shares(p: dict) -> float:
    return abs(p.get("yes_shares", 0.0) - p.get("no_shares", 0.0))


def fmt_money(v: float) -> str:
    return f"${v:+.2f}" if v != 0 else "$0.00"


def load_state(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"paper state not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(path: Path) -> None:
    d = load_state(path)
    closed = d.get("closed_lih_positions", [])
    open_pos = d.get("open_lih_positions", {})

    print("=== account ===")
    print(f"start ${d['starting_balance']:.2f} -> now ${d['current_balance']:.2f}")
    print(f"peak ${d['peak_balance']:.2f}  total_pnl ${d.get('total_pnl', 0):.2f}")
    print(f"lih_pnl ${d.get('lih_pnl', 0):.2f}  dh_pnl ${d.get('dh_pnl', 0):.2f}")
    print(f"lih rounds opened {d.get('total_lih_trades', 0)}  closed {len(closed)}")

    print("\n=== open LIH ===")
    if not open_pos:
        print("(none)")
    else:
        for pid, p in open_pos.items():
            comb = combined_avg(p)
            comb_s = f"{comb:.4f}" if comb is not None else "n/a"
            print(
                f"  {p.get('asset', '?')} gap={gap_shares(p):.1f} "
                f"YES {p.get('yes_shares', 0):.1f} NO {p.get('no_shares', 0):.1f} "
                f"comb={comb_s} rebal=#{p.get('rebalance_count', 0)}"
            )

    print("\n=== closed LIH summary ===")
    if not closed:
        print("(no closed rounds yet)")
        return

    wins = [x for x in closed if x.get("pnl_usdc", 0) > 0]
    losses = [x for x in closed if x.get("pnl_usdc", 0) < 0]
    flat = [x for x in closed if x.get("pnl_usdc", 0) == 0]
    hedged = [x for x in closed if matched_shares(x) > 0]
    rebal_counts = [int(x.get("rebalance_count", 0)) for x in closed]

    print(f"rounds {len(closed)}  W {len(wins)} L {len(losses)} flat {len(flat)}")
    print(f"hedged (both legs) {len(hedged)} / {len(closed)}")
    if rebal_counts:
        print(
            f"rebalance trades/round: min {min(rebal_counts)} "
            f"avg {statistics.mean(rebal_counts):.1f} "
            f"median {statistics.median(rebal_counts):.0f} max {max(rebal_counts)}"
        )

    comb_vals = []
    profitable_comb = 0
    for x in hedged:
        c = combined_avg(x)
        if c is None:
            continue
        comb_vals.append(c)
        if c < 1.0:
            profitable_comb += 1
    if comb_vals:
        print(
            f"combined avg (hedged): min {min(comb_vals):.4f} "
            f"avg {statistics.mean(comb_vals):.4f} max {max(comb_vals):.4f}"
        )
        print(f"hedged with comb<1.0: {profitable_comb}/{len(comb_vals)}")

    by_exit: dict[str, int] = {}
    for x in closed:
        r = x.get("exit_reason", "unknown")
        by_exit[r] = by_exit.get(r, 0) + 1
    print("\nexit reasons:")
    for r, n in sorted(by_exit.items(), key=lambda kv: -kv[1]):
        print(f"  {r}: {n}")

    by_asset: dict[str, dict] = {}
    for x in closed:
        a = x.get("asset", "?")
        s = by_asset.setdefault(a, {"n": 0, "pnl": 0.0, "w": 0, "rebal": []})
        s["n"] += 1
        s["pnl"] += x.get("pnl_usdc", 0.0)
        s["rebal"].append(int(x.get("rebalance_count", 0)))
        if x.get("pnl_usdc", 0) > 0:
            s["w"] += 1
    print("\nby asset:")
    for a, s in sorted(by_asset.items()):
        avg_rebal = statistics.mean(s["rebal"]) if s["rebal"] else 0.0
        print(
            f"  {a}: {s['n']} rounds  W {s['w']}  pnl {fmt_money(s['pnl'])}  "
            f"avg rebal {avg_rebal:.1f}"
        )

    print("\n=== per round (newest last) ===")
    for x in closed:
        m = matched_shares(x)
        c = combined_avg(x)
        comb_s = f"{c:.4f}" if c is not None else "n/a"
        pnl = x.get("pnl_usdc", 0.0)
        print(
            f"  {x.get('asset', '?')} matched={m:.1f} comb={comb_s} "
            f"rebal=#{x.get('rebalance_count', 0)} pnl={fmt_money(pnl)} "
            f"| {x.get('exit_reason', '')}"
        )

    print("\nworst 5:")
    for x in sorted(closed, key=lambda z: z.get("pnl_usdc", 0))[:5]:
        c = combined_avg(x)
        comb_s = f"{c:.4f}" if c is not None else "n/a"
        print(
            f"  {x.get('asset', '?')} comb={comb_s} rebal=#{x.get('rebalance_count', 0)} "
            f"pnl={fmt_money(x.get('pnl_usdc', 0))}"
        )

    print("\nbest 5:")
    for x in sorted(closed, key=lambda z: z.get("pnl_usdc", 0), reverse=True)[:5]:
        c = combined_avg(x)
        comb_s = f"{c:.4f}" if c is not None else "n/a"
        print(
            f"  {x.get('asset', '?')} comb={comb_s} rebal=#{x.get('rebalance_count', 0)} "
            f"pnl={fmt_money(x.get('pnl_usdc', 0))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze LIH paper trading stats")
    parser.add_argument(
        "--path",
        type=Path,
        default=ROOT / "logs" / "paper_state.json",
        help="path to paper_state.json (default: logs/paper_state.json)",
    )
    args = parser.parse_args()
    summarize(args.path)


if __name__ == "__main__":
    main()
