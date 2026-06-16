"""Fetch wallet trade activity from Polymarket Data API (no auth)."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DATA_API = "https://data-api.polymarket.com"


def _infer_asset(title: str) -> str:
    t = (title or "").upper()
    if "BITCOIN" in t or "BTC" in t:
        return "BTC"
    if "ETHEREUM" in t or "ETH" in t:
        return "ETH"
    if "SOLANA" in t or "SOL" in t:
        return "SOL"
    return "—"


def _infer_window_minutes(title: str) -> int | None:
    t = (title or "").lower()
    if "15" in t and ("15m" in t or "15 min" in t or "15-minute" in t):
        return 15
    if "5" in t and ("5m" in t or "5 min" in t or "5-minute" in t):
        return 5
    # Polymarket titles like "1:05AM-1:10AM ET" — infer from clock span.
    import re

    m = re.search(
        r"(\d{1,2}):(\d{2})\s*(am|pm)\s*-\s*(\d{1,2}):(\d{2})\s*(am|pm)",
        t,
    )
    if m:
        def _mins(h: str, mi: str, ap: str) -> int:
            hh = int(h) % 12
            if ap == "pm":
                hh += 12
            return hh * 60 + int(mi)

        start = _mins(m.group(1), m.group(2), m.group(3))
        end = _mins(m.group(4), m.group(5), m.group(6))
        span = (end - start) % (24 * 60)
        if span in (4, 5, 6):
            return 5
        if span in (14, 15, 16):
            return 15
    return None


def parse_market_end_ts(title: str, *, ref_ts: float | None = None) -> float | None:
    """Parse Polymarket up/down window end time from market title (unix seconds)."""
    import calendar
    import re
    import time
    from datetime import datetime

    if not title:
        return None
    ref_ts = ref_ts if ref_ts is not None else time.time()
    t = title.strip()

    clock = re.search(
        r"(\d{1,2}):(\d{2})\s*(am|pm)\s*-\s*(\d{1,2}):(\d{2})\s*(am|pm)",
        t,
        re.I,
    )
    if not clock:
        return None

    def _to_24h(h: str, mi: str, ap: str) -> tuple[int, int]:
        hh = int(h) % 12
        if ap.lower() == "pm":
            hh += 12
        return hh, int(mi)

    end_h, end_m = _to_24h(clock.group(4), clock.group(5), clock.group(6))

    month_day = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})",
        t,
        re.I,
    )
    if not month_day:
        return None

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    month = months[month_day.group(1).lower()]
    day = int(month_day.group(2))
    ref = datetime.utcfromtimestamp(ref_ts)
    year = ref.year
    try:
        end_dt = datetime(year, month, day, end_h, end_m, 0)
    except ValueError:
        return None
    end_ts = calendar.timegm(end_dt.timetuple())
    # ET ≈ UTC-4 (EDT) / UTC-5 (EST) — titles use ET; use -4 for summer markets.
    end_ts += 4 * 3600
    if end_ts < ref_ts - 86400:
        try:
            end_dt = datetime(year + 1, month, day, end_h, end_m, 0)
            end_ts = calendar.timegm(end_dt.timetuple()) + 4 * 3600
        except ValueError:
            pass
    return float(end_ts)


def fetch_user_trades(
    funder_address: str | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return TRADE activity rows sorted newest first."""
    baseline = 0
    raw_base = os.getenv("LIVE_TRADES_BASELINE_TS", "").strip()
    if raw_base:
        try:
            baseline = int(float(raw_base))
        except ValueError:
            baseline = 0

    funder = (funder_address or os.getenv("POLYMARKET_FUNDER", "")).strip()
    if not funder:
        return []
    user = funder if funder.startswith("0x") else f"0x{funder}"
    params = urllib.parse.urlencode(
        {
            "limit": min(max(limit, 1), 1000),
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "user": user,
        }
    )
    url = f"{DATA_API}/activity?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        return [{"error": str(exc)}]

    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [{"error": f"invalid JSON: {exc}"}]

    arr = parsed if isinstance(parsed, list) else (parsed.get("data") or [])
    fills: list[dict[str, Any]] = []
    for a in arr:
        if not isinstance(a, dict) or a.get("type") != "TRADE":
            continue
        title = str(a.get("title") or "")
        ts = int(a.get("timestamp") or 0)
        if baseline > 0 and ts > 0 and ts < baseline:
            continue
        price = float(a.get("price") or 0)
        size = float(a.get("size") or 0)
        usdc = float(a.get("usdcSize") or 0) or (price * size if price and size else 0)
        fills.append(
            {
                "id": str(a.get("id") or a.get("transactionHash") or a.get("orderID") or ""),
                "orderID": str(a.get("orderID") or ""),
                "tokenID": str(a.get("tokenID") or a.get("asset") or ""),
                "side": str(a.get("side") or "").upper(),
                "size": size,
                "price": price,
                "usdcSize": usdc,
                "timestamp": ts,
                "outcome": str(a.get("outcome") or ""),
                "title": title,
                "asset": _infer_asset(title),
                "windowMinutes": _infer_window_minutes(title),
                "transactionHash": str(a.get("transactionHash") or ""),
            }
        )
    return fills
