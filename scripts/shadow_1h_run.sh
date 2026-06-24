#!/usr/bin/env bash
# Start shadow bot, resume, monitor for ~1 hour, write logs/shadow_1h_report.txt
set -uo pipefail
ROOT="/chuan/saibo-trader-trend"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python3}"
LOG="${LOG:-$ROOT/logs/shadow_1h_monitor.log}"
REPORT="${REPORT:-$ROOT/logs/shadow_1h_report.txt}"
DURATION_SEC="${1:-3600}"
INTERVAL_SEC="${2:-300}"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== shadow 1h run start ==="
bash "$ROOT/scripts/server_start_bot.sh" >> "$LOG" 2>&1 || true
sleep 15

if ! pgrep -f './build/trading-core' >/dev/null; then
  log "FAIL: trading-core not running after start"
  exit 1
fi

"$PY" - <<'PY'
from bot_config import write_runtime_config
write_runtime_config({"control": "resume", "reason": "shadow 1h test", "user": "agent"})
print("resume written to runtime_config.json")
PY

sleep 5
log "processes: $(pgrep -af 'start_bot|trading-core' | tr '\n' ' ')"

START=$(date +%s)
END=$((START + DURATION_SEC))
ITER=0

while [ "$(date +%s)" -lt "$END" ]; do
  ITER=$((ITER + 1))
  log "--- tick $ITER ---"
  if ! pgrep -f './build/trading-core' >/dev/null; then
    log "FAIL: trading-core died"
    break
  fi
  log "alive: start_bot=$(pgrep -c -f start_bot.py || echo 0) core=1"
  tail -3 "$ROOT/logs/bridge.log" 2>/dev/null | sed 's/^/  bridge: /' | tee -a "$LOG"
  SHADOW=$(grep -c 'LIVE LIH SHADOW' "$ROOT/bot.log" 2>/dev/null || echo 0)
  ERR=$(grep -cE '\[error\]|\[critical\]|FATAL' "$ROOT/bot.log" 2>/dev/null || echo 0)
  ENTRY=$(grep -c 'entry-wait\|LIH DEBUG' "$ROOT/bot.log" 2>/dev/null || echo 0)
  log "bot.log: SHADOW=$SHADOW errors=$ERR lih_debug=$ENTRY"
  grep 'LIVE LIH SHADOW' "$ROOT/bot.log" 2>/dev/null | tail -2 | sed 's/^/  /' | tee -a "$LOG" || true
  if [ -f "$ROOT/logs/shadow_lih_pnl.csv" ]; then
    log "shadow_pnl rows: $(($(wc -l < "$ROOT/logs/shadow_lih_pnl.csv") - 1))"
  fi
  REMAIN=$((END - $(date +%s)))
  [ "$REMAIN" -le 0 ] && break
  SLEEP=$INTERVAL_SEC
  [ "$REMAIN" -lt "$SLEEP" ] && SLEEP=$REMAIN
  sleep "$SLEEP"
done

{
  echo "=== Shadow Run Report $(date -u -Iseconds) ==="
  echo "Duration target: ${DURATION_SEC}s | ticks: $ITER"
  echo ""
  echo "Processes:"
  pgrep -af 'start_bot|trading-core' || echo "(none)"
  echo ""
  echo "Counts in bot.log:"
  echo "  SHADOW lines: $(grep -c 'LIVE LIH SHADOW' "$ROOT/bot.log" 2>/dev/null || echo 0)"
  echo "  errors/critical: $(grep -cE '\[error\]|\[critical\]|FATAL' "$ROOT/bot.log" 2>/dev/null || echo 0)"
  echo "  LIH DEBUG: $(grep -c 'LIH DEBUG' "$ROOT/bot.log" 2>/dev/null || echo 0)"
  echo ""
  echo "Last 5 SHADOW:"
  grep 'LIVE LIH SHADOW' "$ROOT/bot.log" 2>/dev/null | tail -5 || echo "(none)"
  echo ""
  echo "Last 5 LIH DEBUG:"
  grep 'LIH DEBUG' "$ROOT/bot.log" 2>/dev/null | tail -5 || echo "(none)"
  echo ""
  echo "Last 5 errors:"
  grep -iE '\[error\]|\[critical\]|FATAL' "$ROOT/bot.log" 2>/dev/null | tail -5 || echo "(none)"
  echo ""
  if [ -f "$ROOT/logs/shadow_lih_pnl.csv" ]; then
    echo "shadow_lih_pnl.csv:"
    tail -5 "$ROOT/logs/shadow_lih_pnl.csv"
  else
    echo "shadow_lih_pnl.csv: (not created yet)"
  fi
  echo ""
  "$PY" "$ROOT/scripts/shadow_pnl_summary.py" 2>/dev/null || true
} | tee "$REPORT"

log "=== done → $REPORT ==="
