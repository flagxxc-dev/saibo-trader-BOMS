#!/bin/bash
# Fast Next.js restart (no npm ci / build). Use server_start_web.sh for full deploy.
set -euo pipefail
ROOT="/opt/polymarket-bot"
cd "$ROOT/frontend"

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
export IP_SECURITY_PATH="${IP_SECURITY_PATH:-$ROOT/logs/ip_security.json}"
export BOT_WS_URL="${BOT_WS_URL:-ws://127.0.0.1:8080}"
export BOT_API_URL="${BOT_API_URL:-http://127.0.0.1:8081}"
export BOT_API_TOKEN="${BOT_API_TOKEN:-}"
export PORT="${PORT:-3001}"
export HOSTNAME=0.0.0.0
export NEXTAUTH_URL="${NEXTAUTH_URL:-http://127.0.0.1:3001}"
export NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-change-me-in-production}"
export AUTH_TRUST_HOST=true

if [ -z "${AUTH_USERNAME:-}" ] || [ -z "${AUTH_PASSWORD:-}" ]; then
  echo "ERROR: Set AUTH_USERNAME and AUTH_PASSWORD in $ROOT/web.env before restarting web." >&2
  exit 1
fi
export AUTH_SECRET="${NEXTAUTH_SECRET}"

# Skip heavy prisma on routine restarts (watchdog / manual). Set WEB_RUN_MIGRATE=1 to force.
if [ "${WEB_SKIP_PRISMA:-1}" != "1" ] || [ "${WEB_RUN_MIGRATE:-0}" = "1" ]; then
  npx prisma generate
  npx prisma db push
  npx tsx prisma/seed.ts
fi

bash "$ROOT/scripts/web_run.sh"
