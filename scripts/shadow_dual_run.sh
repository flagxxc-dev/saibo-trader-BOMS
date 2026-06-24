#!/usr/bin/env bash
set -uo pipefail
ROOT="/chuan/saibo-trader-trend"
cd "$ROOT"
PY="$ROOT/.venv/bin/python3"
DURATION_SEC="${1:-0}"
FEED_MODE="${SHADOW_FEED_MODE:-rest}"
LOG="$ROOT/logs/shadow_dual_monitor.log"
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== shadow dual strategies start (duration=${DURATION_SEC}s feed=${FEED_MODE}) ==="
pkill -f 'start_bot.py' 2>/dev/null || true
pkill -f './build/trading-core' 2>/dev/null || true
sleep 2

FEED_ARG=""
if [ "$FEED_MODE" = "ws" ]; then
  FEED_ARG="--feed ws --phase ${SHADOW_PHASE_ID:-2}"
fi

if [ "$DURATION_SEC" -gt 0 ] 2>/dev/null; then
  exec "$PY" -u "$ROOT/scripts/shadow_dual_strategies.py" "$DURATION_SEC" $FEED_ARG 2>&1 | tee -a "$LOG"
else
  exec "$PY" -u "$ROOT/scripts/shadow_dual_strategies.py" $FEED_ARG 2>&1 | tee -a "$LOG"
fi
