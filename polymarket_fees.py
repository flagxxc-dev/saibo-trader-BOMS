"""Polymarket CLOB fee helpers (V2 curve, not flat feeRateBps on orders)."""

from __future__ import annotations

import math
import os
from typing import Any

import requests

CLOB_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com").rstrip("/")
GAMMA_HOST = "https://gamma-api.polymarket.com"


def effective_platform_fee_per_share(price: float, fee_rate: float, fee_exponent: float) -> float:
    """USDC fee per 1 share at `price` (matches py-clob-client-v2 fees.py curve)."""
    if price <= 0 or price >= 1 or fee_rate <= 0:
        return 0.0
    return fee_rate * (price * (1.0 - price)) ** fee_exponent


def dh_entry_fee_per_share(
    yes_price: float,
    no_price: float,
    yes_fee: dict[str, float],
    no_fee: dict[str, float],
) -> float:
    """Total platform fee per share-pair for DH (YES leg + NO leg)."""
    y = effective_platform_fee_per_share(yes_price, yes_fee.get("rate", 0.0), yes_fee.get("exponent", 0.0))
    n = effective_platform_fee_per_share(no_price, no_fee.get("rate", 0.0), no_fee.get("exponent", 0.0))
    return y + n


def flat_fee_per_share(combined: float, flat_rate: float) -> float:
    """Legacy bot model: combined * FEE_RATE."""
    return combined * flat_rate


def fetch_clob_market(condition_id: str, timeout: float = 10.0) -> dict[str, Any]:
    url = f"{CLOB_HOST}/clob-markets/{condition_id}"
    res = requests.get(url, timeout=timeout)
    res.raise_for_status()
    return res.json()


def parse_token_fees(clob_market: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Map token_id -> {rate, exponent, base_fee_bps} from /clob-markets response."""
    fd = clob_market.get("fd") or {}
    rate = float(fd.get("r") or 0.0)
    exponent = float(fd.get("e") or 0.0)
    out: dict[str, dict[str, float]] = {}
    for token in clob_market.get("t") or []:
        if not token or not isinstance(token, dict):
            continue
        tid = str(token.get("t") or "")
        if not tid:
            continue
        out[tid] = {"rate": rate, "exponent": exponent, "base_fee_bps": 0.0}
    return out


def fetch_token_fee_rate_bps(token_id: str, timeout: float = 10.0) -> int:
    """V1-style /fee-rate (informational; V2 orders do not sign feeRateBps)."""
    url = f"{CLOB_HOST}/fee-rate"
    res = requests.get(url, params={"token_id": token_id}, timeout=timeout)
    res.raise_for_status()
    data = res.json()
    return int(data.get("base_fee") or 0)


def sample_updown_market(timeout: float = 12.0) -> dict[str, Any] | None:
    """Pick one active BTC 5m market for fee / preflight samples (matches C++ slug probe)."""
    import json
    import time

    url = f"{GAMMA_HOST}/events"
    now = int(time.time())
    window = 300
    # Try current and next 5m window slugs (Polymarket uses btc-updown-5m-{unix_ts})
    candidates = [now // window * window, (now // window + 1) * window]
    try:
        for ts in candidates:
            slug = f"btc-updown-5m-{ts}"
            res = requests.get(url, params={"slug": slug, "limit": 1}, timeout=timeout)
            res.raise_for_status()
            events = res.json()
            if not events:
                continue
            ev = events[0]
            markets = ev.get("markets") or []
            if not markets:
                continue
            m = markets[0]
            tokens = m.get("clobTokenIds") or m.get("clob_token_ids")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if not tokens or len(tokens) < 2:
                continue
            return {
                "question": m.get("question") or ev.get("title") or "",
                "condition_id": m.get("conditionId") or m.get("condition_id") or "",
                "yes_token_id": str(tokens[0]),
                "no_token_id": str(tokens[1]),
                "yes_price": 0.5,
                "no_price": 0.5,
            }
    except Exception:
        return None
    return None


def compare_fee_models(
    yes_price: float,
    no_price: float,
    fee_rate: float,
    fee_exponent: float,
    env_flat_rate: float,
) -> dict[str, Any]:
    combined = yes_price + no_price
    dynamic = dh_entry_fee_per_share(
        yes_price,
        no_price,
        {"rate": fee_rate, "exponent": fee_exponent},
        {"rate": fee_rate, "exponent": fee_exponent},
    )
    flat = flat_fee_per_share(combined, env_flat_rate)
    discount_dynamic = 1.0 - combined - dynamic
    discount_flat = 1.0 - combined - flat
    return {
        "combined": combined,
        "dynamic_fee_per_share": dynamic,
        "flat_fee_per_share": flat,
        "discount_dynamic_pct": discount_dynamic * 100.0,
        "discount_flat_pct": discount_flat * 100.0,
        "fee_rate": fee_rate,
        "fee_exponent": fee_exponent,
        "env_flat_rate": env_flat_rate,
    }
