#!/bin/bash
# Restart Next.js only (no npm ci / build). Reads /opt/polymarket-bot/web.env
set -euo pipefail
ROOT="/opt/polymarket-bot"
cd "$ROOT/frontend"

if [ -f "$ROOT/web.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/web.env"
  set +a
fi

export DATABASE_URL="${DATABASE_URL:-file:./prisma/data/dev.db}"
export BOT_WS_URL="${BOT_WS_URL:-ws://127.0.0.1:8080}"
export BOT_API_URL="${BOT_API_URL:-http://127.0.0.1:8081}"
export PORT="${PORT:-3001}"
export HOSTNAME=0.0.0.0
export NEXTAUTH_URL="${NEXTAUTH_URL:-http://127.0.0.1:3001}"
export NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-change-me-in-production}"
export AUTH_TRUST_HOST=true

npx prisma generate
npx prisma db push
npx tsx prisma/seed.ts

pkill -f "next-server" 2>/dev/null || true
sleep 2
setsid -f -- npm run start >> "$ROOT/logs/frontend.log" 2>&1
sleep 4
ss -tlnp | grep ":${PORT}" || true
