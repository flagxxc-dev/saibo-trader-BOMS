"""
Shared market timing utilities used by EdgeDetector and DumpHedgeDetector.
"""

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.polymarket_client import MarketInfo

from utils.logger import get_logger

logger = get_logger(__name__)


def get_seconds_remaining(market: "MarketInfo", window_seconds: float) -> Optional[float]:
    """
    Estimate seconds remaining in the current market window.

    Priority:
      1. end_date_iso from Gamma API (if it's not midnight UTC — CLOB API bug)
      2. Fallback: align to nearest UTC window boundary
    """
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)
    ws = int(window_seconds)

    end_str = market.end_date_iso
    if end_str:
        try:
            clean = end_str.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(clean)
            is_midnight = end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0
            if not is_midnight:
                remaining = (end_dt - now_dt).total_seconds()
                if -60 < remaining < ws + 90:
                    return max(0.0, remaining)
        except Exception:
            pass

    now_ts = now_dt.timestamp()
    window_open = (int(now_ts) // ws) * ws
    remaining = (window_open + ws) - now_ts
    return max(0.0, remaining)
