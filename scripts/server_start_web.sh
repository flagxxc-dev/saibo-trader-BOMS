#!/bin/bash
# Next.js dashboard (bare metal, survives SSH disconnect)
set -e
ROOT="/opt/polymarket-bot"
cd "$ROOT/frontend"
mkdir -p prisma/data "$ROOT/logs"

# Optional: /opt/polymarket-bot/web.env (AUTH_USERNAME, AUTH_PASSWORD, NEXTAUTH_SECRET)
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
if [ -z "${NEXTAUTH_SECRET:-}" ] || [ "${NEXTAUTH_SECRET}" = "change-me-in-production" ]; then
  NEXTAUTH_SECRET="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
fi
export NEXTAUTH_SECRET
export AUTH_USERNAME="${AUTH_USERNAME:-admin}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-admin}"
export AUTH_TRUST_HOST=true
# Persist secret for restarts (web.env is gitignored on server)
if [ -f "$ROOT/web.env" ]; then
  if grep -q '^NEXTAUTH_SECRET=' "$ROOT/web.env"; then
    sed -i "s/^NEXTAUTH_SECRET=.*/NEXTAUTH_SECRET=${NEXTAUTH_SECRET}/" "$ROOT/web.env"
  else
    echo "NEXTAUTH_SECRET=${NEXTAUTH_SECRET}" >> "$ROOT/web.env"
  fi
else
  printf 'NEXTAUTH_SECRET=%s\nAUTH_TRUST_HOST=true\n' "$NEXTAUTH_SECRET" > "$ROOT/web.env"
fi

npm ci --no-audit --no-fund
npx prisma generate
npx prisma db push
npx tsx prisma/seed.ts

npm run build

pkill -f "next-server" 2>/dev/null || true
pkill -f "$ROOT/frontend" 2>/dev/null || true
sleep 2
setsid -f -- npm run start >> "$ROOT/logs/frontend.log" 2>&1
sleep 6
ss -tlnp | grep ":${PORT}" || true
pgrep -af "next|node.*${PORT}" || true
