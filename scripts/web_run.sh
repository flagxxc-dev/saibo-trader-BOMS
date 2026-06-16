#!/bin/bash
# Start Next.js with minimal RAM (standalone when available).
set -euo pipefail
ROOT="/opt/polymarket-bot"
FRONTEND="$ROOT/frontend"
STANDALONE="$FRONTEND/.next/standalone"
PORT="${PORT:-3001}"

if [ -f "$ROOT/web.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/web.env"
  set +a
fi
if [ -z "${BOT_API_TOKEN:-}" ] && [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export DATABASE_URL="${DATABASE_URL:-file:./prisma/data/dev.db}"
export BOT_WS_URL="${BOT_WS_URL:-ws://127.0.0.1:8080}"
export BOT_API_URL="${BOT_API_URL:-http://127.0.0.1:8081}"
export BOT_API_TOKEN="${BOT_API_TOKEN:-}"
export PORT
export HOSTNAME="${HOSTNAME:-0.0.0.0}"
export NEXTAUTH_URL="${NEXTAUTH_URL:-http://127.0.0.1:${PORT}}"
export AUTH_TRUST_HOST="${AUTH_TRUST_HOST:-true}"
export AUTH_SECRET="${NEXTAUTH_SECRET:-change-me-in-production}"
export NODE_ENV=production
# Small VPS: cap runtime heap + shrink libuv pool
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=192}"
export UV_THREADPOOL_SIZE="${UV_THREADPOOL_SIZE:-2}"

if [ ! -d "$FRONTEND/.next" ]; then
  echo "ERROR: missing $FRONTEND/.next — run server_start_web.sh first" >&2
  exit 1
fi

pkill -9 -f "next-server" 2>/dev/null || true
pkill -f "node.*standalone/server.js" 2>/dev/null || true
sleep 2

if [ -f "$STANDALONE/server.js" ]; then
  mkdir -p "$STANDALONE/.next"
  rsync -a --delete "$FRONTEND/.next/static/" "$STANDALONE/.next/static/" 2>/dev/null \
    || cp -r "$FRONTEND/.next/static" "$STANDALONE/.next/static"
  if [ -d "$FRONTEND/public" ]; then
    rsync -a "$FRONTEND/public/" "$STANDALONE/public/" 2>/dev/null \
      || cp -r "$FRONTEND/public" "$STANDALONE/public"
  fi
  cd "$STANDALONE"
  echo "[web_run] standalone node server.js PORT=$PORT heap=${NODE_OPTIONS}"
  setsid -f -- node server.js >> "$ROOT/logs/frontend.log" 2>&1
else
  cd "$FRONTEND"
  echo "[web_run] fallback npm run start PORT=$PORT heap=${NODE_OPTIONS}"
  setsid -f -- npm run start >> "$ROOT/logs/frontend.log" 2>&1
fi

sleep 4
ss -tlnp | grep ":${PORT}" || { echo "ERROR: port ${PORT} not listening"; exit 1; }
