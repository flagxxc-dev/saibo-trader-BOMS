#!/bin/bash
# Install cron watchdog (every 3 minutes) for Next.js on small VPS.
set -euo pipefail
ROOT="/opt/polymarket-bot"
MARK="# polymarket-web-watchdog"
CRON_LINE="*/3 * * * * $ROOT/scripts/web_watchdog.sh"
TMP="$(mktemp)"

chmod +x "$ROOT/scripts/web_watchdog.sh" "$ROOT/scripts/web_run.sh" "$ROOT/scripts/server_restart_web.sh"

( crontab -l 2>/dev/null | grep -v "$MARK" || true
  echo "$CRON_LINE $MARK"
) > "$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "Installed cron watchdog:"
crontab -l | grep "$MARK" || true
