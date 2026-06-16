#!/usr/bin/env python3
"""Rebuild open LIH leg1 state from Polymarket activity when C++ missed fills."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from clob_trades import fetch_user_trades, parse_market_end_ts  # noqa: E402


def _outcome_side(outcome: str) -> str:
    o = (outcome or "").strip().lower()
    if o in ("yes", "up"):
        return "yes"
    if o in ("no", "down"):
        return "no"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune-only", action="store_true", help="only drop expired open rows")
    args = parser.parse_args()

    if args.prune_only:
        sys.path.insert(0, str(ROOT / "scripts"))
        from prune_live_lih import prune_live_state

        prune_live_state()
        return 0

    live_path = Path(os.getenv("LIVE_STATE_PATH", "logs/live_state.json"))
    now = time.time()
    trades = fetch_user_trades(limit=80)
    if trades and isinstance(trades[0], dict) and trades[0].get("error"):
        print("ERROR:", trades[0]["error"], file=sys.stderr)
        return 1

    # One LIH slot per asset+window+market (merge YES/NO legs into one row).
    slots: dict[str, dict] = {}
    for t in trades:
        if str(t.get("side", "")).upper() != "BUY":
            continue
        title = str(t.get("title") or "")
        end_ts = parse_market_end_ts(title, ref_ts=now)
        if end_ts and now > end_ts + 30:
            continue
        asset = str(t.get("asset") or "").upper()
        if asset == "—":
            asset = ""
        asset = asset.lower() if asset else ""
        window = t.get("windowMinutes")
        if window not in (5, 15):
            window = None
        if not asset or window not in (5, 15):
            from clob_trades import _infer_asset, _infer_window_minutes

            asset = asset or _infer_asset(title)
            window = window or _infer_window_minutes(title)
        asset = (asset or "").lower()
        if not asset or asset == "—" or window not in (5, 15):
            continue
        leg = _outcome_side(str(t.get("outcome") or ""))
        if not leg:
            continue
        token_id = str(t.get("tokenID") or "")
        slot_key = f"{asset}|{int(window)}|{int(end_ts) if end_ts else title}"
        row = slots.setdefault(
            slot_key,
            {
                "asset": asset,
                "window_minutes": int(window),
                "title": title,
                "end_date_ts": end_ts or 0.0,
                "opened_at": float(t.get("timestamp") or 0),
                "yes_shares": 0.0,
                "no_shares": 0.0,
                "yes_cost": 0.0,
                "no_cost": 0.0,
                "yes_token_id": "",
                "no_token_id": "",
            },
        )
        shares = float(t.get("size") or 0)
        cost = float(t.get("usdcSize") or 0) or (
            float(t.get("price") or 0) * shares
        )
        if leg == "yes":
            row["yes_shares"] += shares
            row["yes_cost"] += cost
            if token_id:
                row["yes_token_id"] = token_id
        else:
            row["no_shares"] += shares
            row["no_cost"] += cost
            if token_id:
                row["no_token_id"] = token_id
        ts = float(t.get("timestamp") or 0)
        if ts and (not row["opened_at"] or ts < row["opened_at"]):
            row["opened_at"] = ts

    if not slots:
        print("No active LIH-style BUY trades to reconcile.")
        return 0

    doc: dict = {
        "version": 1,
        "saved_at": now,
        "current_balance": 0.0,
        "total_lih_trades": 0,
        "lih_pnl": 0.0,
        "closed_lih_positions": [],
        "lih_leg1_inflight": [],
    }
    if live_path.is_file():
        try:
            existing = json.loads(live_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                doc["current_balance"] = float(existing.get("current_balance") or 0)
                doc["total_lih_trades"] = int(existing.get("total_lih_trades") or 0)
                doc["lih_pnl"] = float(existing.get("lih_pnl") or 0)
                if isinstance(existing.get("closed_lih_positions"), list):
                    doc["closed_lih_positions"] = existing["closed_lih_positions"]
        except json.JSONDecodeError:
            pass

    if doc["current_balance"] <= 0:
        try:
            from fetch_balance import fetch_usdc_balance  # type: ignore

            doc["current_balance"] = float(fetch_usdc_balance())
        except Exception:
            pass

    open_lih: dict = {}
    for _key, row in slots.items():
        total_shares = row["yes_shares"] + row["no_shares"]
        if total_shares <= 0:
            continue
        total_cost = row["yes_cost"] + row["no_cost"]
        yes_avg = row["yes_cost"] / row["yes_shares"] if row["yes_shares"] > 0 else 0.0
        no_avg = row["no_cost"] / row["no_shares"] if row["no_shares"] > 0 else 0.0
        lih_id = f"LIH-{row['asset']}-{int(row['opened_at'] * 1000)}-recon"
        pos = {
            "lih_id": lih_id,
            "asset": row["asset"],
            "market_question": row["title"],
            "yes_token_id": row["yes_token_id"],
            "no_token_id": row["no_token_id"],
            "window_minutes": row["window_minutes"],
            "yes_shares": row["yes_shares"],
            "no_shares": row["no_shares"],
            "yes_cost": row["yes_cost"],
            "no_cost": row["no_cost"],
            "entry_fees": total_cost * 0.018,
            "opened_at": row["opened_at"] or now,
            "end_date_ts": row["end_date_ts"] or 0.0,
            "rebalance_count": 0,
            "is_neg_risk": False,
            "paper_mode": False,
            "exit_reason": "",
        }
        open_lih[lih_id] = pos
        held = "YES" if row["yes_shares"] > row["no_shares"] + 1e-6 else (
            "NO" if row["no_shares"] > row["yes_shares"] + 1e-6 else "BOTH"
        )
        print(
            f"reconcile {row['asset']} {row['window_minutes']}m {held} "
            f"Y={row['yes_shares']:.2f}@{yes_avg:.4f} N={row['no_shares']:.2f}@{no_avg:.4f} "
            f"(${total_cost:.2f})"
        )

    doc["open_lih_positions"] = open_lih
    doc["total_lih_trades"] = max(int(doc.get("total_lih_trades") or 0), len(open_lih))
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(open_lih)} active open LIH slot(s) -> {live_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
