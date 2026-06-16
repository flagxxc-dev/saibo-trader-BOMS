#!/usr/bin/env python3
"""Check why LIH leg1 may not open: prices, time window, depth."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def probe_slug(asset: str, window_min: int, ts: int) -> dict | None:
    slug = f"{asset}-updown-{window_min}m-{ts}"
    try:
        res = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=12)
        res.raise_for_status()
        events = res.json()
        if not events:
            return None
        markets = events[0].get("markets") or []
        if not markets:
            return None
        m = markets[0]
        tokens = m.get("clobTokenIds") or m.get("clob_token_ids")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if not tokens or len(tokens) < 2:
            return None
        end = m.get("endDate") or events[0].get("endDate")
        return {
            "slug": slug,
            "question": m.get("question") or events[0].get("title") or slug,
            "yes_token": str(tokens[0]),
            "no_token": str(tokens[1]),
            "end_ts": _parse_ts(end),
        }
    except Exception:
        return None


def _parse_ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    if s.isdigit():
        return float(s)
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def best_ask(token_id: str) -> tuple[float, float]:
    try:
        res = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=12)
        res.raise_for_status()
        book = res.json()
        asks = book.get("asks") or []
        if not asks:
            return 0.0, 0.0
        best = min(float(a["price"]) for a in asks if float(a.get("price", 0)) > 0)
        depth = sum(float(a.get("size", 0)) for a in asks if float(a.get("price", 0)) <= best * 1.05)
        return best, depth
    except Exception:
        return 0.0, 0.0


def main() -> None:
    env = load_env()
    leg1_max = float(env.get("LIH_LEG1_MAX_PRICE", "0.45"))
    min_secs = float(env.get("LIH_MIN_SECONDS_REMAINING", "90"))
    leg1_shares = float(env.get("LIH_LEG1_SHARES", "10"))
    assets = []
    for a in ("btc", "eth", "sol"):
        if env.get(f"DH_ENABLE_5M_{a.upper()}", "true").lower() not in ("false", "0", "no", "off"):
            assets.append(a)

    now = time.time()
    boundary = 300
    base_ts = int(now // boundary) * boundary
    candidates = [base_ts, base_ts + boundary, base_ts - boundary]

    print("=== LIH entry diagnostic ===")
    print(f"leg1_max={leg1_max}  min_secs_left>={min_secs}  leg1_shares={leg1_shares}")
    print(f"assets={assets}\n")

    found = 0
    for asset in assets:
        for ts in candidates:
            mkt = probe_slug(asset, 5, ts)
            if not mkt or mkt["end_ts"] <= now:
                continue
            secs_left = mkt["end_ts"] - now
            yes_ask, yes_depth = best_ask(mkt["yes_token"])
            no_ask, no_depth = best_ask(mkt["no_token"])
            yes_cheap = yes_ask > 0 and yes_ask <= leg1_max
            no_cheap = no_ask > 0 and no_ask <= leg1_max
            reasons = []
            if secs_left < min_secs:
                reasons.append(f"time<{min_secs:.0f}s")
            if yes_ask <= 0 or no_ask <= 0:
                reasons.append("missing quote")
            if not yes_cheap and not no_cheap:
                reasons.append("no cheap leg")
            pick = None
            if yes_cheap and (not no_cheap or yes_ask <= no_ask):
                pick = ("YES", yes_ask, yes_depth)
            elif no_cheap:
                pick = ("NO", no_ask, no_depth)
            if pick:
                side, px, depth = pick
                if depth < leg1_shares:
                    reasons.append(f"depth {depth:.1f} < {leg1_shares:.0f}")
            status = "WOULD OPEN" if not reasons and pick else "BLOCKED"
            print(f"[{asset}] {mkt['question']}")
            print(f"  slug={mkt['slug']}")
            print(f"  book YES {yes_ask:.4f} (depth {yes_depth:.1f})  NO {no_ask:.4f} (depth {no_depth:.1f})  sum {yes_ask+no_ask:.4f}")
            print(f"  secs_left={secs_left:.0f}  -> {status}" + (f" ({', '.join(reasons)})" if reasons else f" pick {pick[0]}@{pick[1]:.4f}"))
            print()
            found += 1
    if not found:
        print("No active 5m markets found via slug probe.")


if __name__ == "__main__":
    main()
