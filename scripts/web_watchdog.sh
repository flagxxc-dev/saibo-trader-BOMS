#!/bin/bash
# Lightweight health check; restart web if login page unreachable.
set -euo pipefail
ROOT="/opt/polymarket-bot"
PORT="${PORT:-3001}"
LOG="$ROOT/logs/web_watchdog.log"
URL="http://127.0.0.1:${PORT}/login"
STAMP="$(date -Is 2>/dev/null || date)"

if curl -sf --max-time 8 "$URL" >/dev/null 2>&1; then
  exit 0
fi

echo "$STAMP web down (curl $URL failed), restarting..." >> "$LOG"
# Quick restart: no prisma/build
export WEB_SKIP_PRISMA=1
bash "$ROOT/scripts/server_restart_web.sh" >> "$LOG" 2>&1 || true

sleep 5
if curl -sf --max-time 8 "$URL" >/dev/null 2>&1; then
  echo "$STAMP web recovered" >> "$LOG"
else
  echo "$STAMP web still down after restart" >> "$LOG"
fi
