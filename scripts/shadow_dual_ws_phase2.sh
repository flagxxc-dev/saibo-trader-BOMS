#!/usr/bin/env bash
# Write phase-1 cutover marker and start phase-2 WS shadow for remaining 9h window.
set -uo pipefail
ROOT="/chuan/saibo-trader-trend"
cd "$ROOT"
PY="$ROOT/.venv/bin/python3"
LOG="$ROOT/logs/shadow_dual_monitor.log"

# Original 9h run started ~2026-06-24 02:03:59 UTC (unix 1782266639)
PHASE1_START="${SHADOW_PHASE1_START_TS:-1782266639}"
TOTAL_DURATION="${SHADOW_TOTAL_DURATION_SEC:-32400}"
REMAIN=$(( PHASE1_START + TOTAL_DURATION - $(date +%s) ))
if [ "$REMAIN" -lt 60 ]; then
  echo "Remaining time ${REMAIN}s too short; extend SHADOW_TOTAL_DURATION_SEC or adjust PHASE1_START_TS" >&2
  exit 1
fi

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== PHASE_CUTOVER: stopping any shadow_dual ==="
pkill -f 'shadow_dual_strategies.py' 2>/dev/null || true
sleep 2

"$PY" - <<'PY'
import json, time
from datetime import datetime, timezone
from pathlib import Path

root = Path("/chuan/saibo-trader-trend")
logs = root / "logs"
p1_start = float(__import__("os").environ.get("SHADOW_PHASE1_START_TS", "1782266639"))
now = time.time()
marker = {
    "phase1": {
        "feed": "rest_4s",
        "poll_sec": 4,
        "start_ts": p1_start,
        "start_utc": datetime.fromtimestamp(p1_start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "end_ts": now,
        "end_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "note": "REST poll shadow before WS cutover",
    },
}
(logs / "shadow_dual_phase_marker.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
line = (
    f"=== PHASE_CUTOVER phase=1_end feed=rest_4s ts={now:.0f} "
    f"utc={marker['phase1']['end_utc']} ==="
)
with (logs / "shadow_dual.log").open("a", encoding="utf-8") as f:
    f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {line}\n")
print(line)
PY

log "=== starting phase-2 WS shadow remaining=${REMAIN}s ==="
export SHADOW_FEED_MODE=ws
export SHADOW_PHASE_ID=2
exec "$PY" -u "$ROOT/scripts/shadow_dual_strategies.py" "$REMAIN" --feed ws --phase 2 2>&1 | tee -a "$LOG"
