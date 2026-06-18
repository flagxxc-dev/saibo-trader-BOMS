#!/bin/bash
# Install hourly disk_guard cron on the VPS (idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="${PROJ:-$(cd "$SCRIPT_DIR/.." && pwd)}"
GUARD="$PROJ/scripts/disk_guard.sh"
LOG="$PROJ/logs/disk_guard.log"
MARKER="# polymarket-bot disk_guard"
CRON_SCHEDULE="${DISK_GUARD_CRON:-15 */4 * * *}"
CRON_LINE="$CRON_SCHEDULE PROJ=$PROJ $GUARD >> $LOG 2>&1 $MARKER"

chmod +x "$GUARD"
mkdir -p "$PROJ/logs"

tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v "$MARKER" | grep -v 'scripts/disk_guard.sh' >"$tmp" || true
echo "$CRON_LINE" >>"$tmp"
crontab "$tmp"
rm -f "$tmp"

echo "Installed disk_guard cron:"
crontab -l | grep disk_guard || true
echo ""
echo "Threshold: ${DISK_GUARD_THRESHOLD:-95}% on ${DISK_GUARD_PATH:-/}"
echo "Test dry-run:  DISK_GUARD_DRY_RUN=1 DISK_GUARD_FORCE=1 bash $GUARD"
echo "Test cleanup:  DISK_GUARD_FORCE=1 bash $GUARD"
