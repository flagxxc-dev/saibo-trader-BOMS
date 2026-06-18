#!/usr/bin/env python3
"""Drop expired open LIH rows from live_state.json (markets already settled)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from clob_trades import parse_market_end_ts  # noqa: E402


def prune_live_state(path: Path | None = None, *, grace_sec: float = 30.0) -> int:
    path = path or Path(os.getenv("LIVE_STATE_PATH", "logs/live_state.json"))
    if not path.is_file():
        print("no live_state")
        return 0

    now = time.time()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if int(doc.get("version") or 0) != 1:
        print("skip: bad version")
        return 0

    open_lih = doc.get("open_lih_positions") or {}
    if not isinstance(open_lih, dict):
        return 0

    kept: dict = {}
    closed = list(doc.get("closed_lih_positions") or [])
    removed = 0
    for lid, pos in open_lih.items():
        if not isinstance(pos, dict):
            continue
        title = str(pos.get("market_question") or "")
        end_ts = float(pos.get("end_date_ts") or 0)
        opened_at = float(pos.get("opened_at") or 0)
        win_min = int(pos.get("window_minutes") or 5)
        if end_ts <= 0:
            parsed = parse_market_end_ts(title, ref_ts=now)
            if parsed:
                end_ts = parsed
                pos["end_date_ts"] = end_ts
        stale_by_open = opened_at > 0 and now > opened_at + win_min * 60 + grace_sec
        bogus_end = end_ts > now + 86400
        expired = (end_ts > 0 and now > end_ts + grace_sec) or stale_by_open or bogus_end
        if expired:
            pos = dict(pos)
            pos["closed_at"] = now
            pos["exit_reason"] = "expired (pruned)"
            closed.append(pos)
            removed += 1
            print(f"prune expired {pos.get('asset')} | {title[:50]}")
            continue
        kept[lid] = pos

    if removed == 0:
        print("prune: nothing expired")
        return 0

    doc["open_lih_positions"] = kept
    doc["closed_lih_positions"] = closed[-200:]
    doc["saved_at"] = now
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"prune: removed {removed}, open={len(kept)}")
    return removed


def main() -> int:
    prune_live_state()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
