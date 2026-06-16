#!/bin/bash
# Next.js dashboard full install + build (run rarely). Daily runtime uses server_restart_web.sh.
set -e
ROOT="/opt/polymarket-bot"
cd "$ROOT/frontend"
mkdir -p prisma/data "$ROOT/logs"

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
export PORT="${PORT:-3001}"
export HOSTNAME=0.0.0.0
export NEXTAUTH_URL="${NEXTAUTH_URL:-http://127.0.0.1:3001}"
if [ -z "${NEXTAUTH_SECRET:-}" ] || [ "${NEXTAUTH_SECRET}" = "change-me-in-production" ]; then
  NEXTAUTH_SECRET="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi
export NEXTAUTH_SECRET
export AUTH_SECRET="${NEXTAUTH_SECRET}"
export AUTH_TRUST_HOST=true

if [ -z "${AUTH_USERNAME:-}" ] || [ -z "${AUTH_PASSWORD:-}" ]; then
  echo "ERROR: Set AUTH_USERNAME and AUTH_PASSWORD in $ROOT/web.env before starting web." >&2
  exit 1
fi

{
  echo "AUTH_USERNAME=${AUTH_USERNAME}"
  echo "AUTH_PASSWORD=${AUTH_PASSWORD}"
  echo "NEXTAUTH_SECRET=${NEXTAUTH_SECRET}"
  echo "AUTH_TRUST_HOST=true"
  echo "NEXTAUTH_URL=${NEXTAUTH_URL}"
  echo "BOT_WS_URL=${BOT_WS_URL}"
  echo "BOT_API_URL=${BOT_API_URL}"
  if [ -n "${BOT_API_TOKEN:-}" ]; then
    echo "BOT_API_TOKEN=${BOT_API_TOKEN}"
  fi
} > "$ROOT/web.env"

export IP_SECURITY_PATH="${IP_SECURITY_PATH:-$ROOT/logs/ip_security.json}"
export BLOCK_IP_SCRIPT="${BLOCK_IP_SCRIPT:-$ROOT/scripts/block_ip.sh}"
export APPLY_FIREWALL_BLOCK="${APPLY_FIREWALL_BLOCK:-true}"

chmod +x "$ROOT/scripts/block_ip.sh" "$ROOT/scripts/apply_ip_blacklist.sh" 2>/dev/null || true
bash "$ROOT/scripts/apply_ip_blacklist.sh" 2>/dev/null || true

npm ci --no-audit --no-fund
export WEB_RUN_MIGRATE=1
export WEB_SKIP_PRISMA=0
npx prisma generate
npx prisma db push
npx tsx prisma/seed.ts

# Build may spike RAM; allow more heap only for build step
export NODE_OPTIONS="--max-old-space-size=512"
npm run build
unset NODE_OPTIONS

chmod +x "$ROOT/scripts/web_run.sh" "$ROOT/scripts/web_watchdog.sh" "$ROOT/scripts/web_install_watchdog.sh" 2>/dev/null || true
bash "$ROOT/scripts/web_run.sh"

# Auto-heal if process dies overnight
bash "$ROOT/scripts/web_install_watchdog.sh" 2>/dev/null || true
