#!/bin/bash
# Permanently block MongoDB — C++ polymarket-bot does not use it.
# polycopy cron health script was auto-starting mongod every 30 minutes.
set -euo pipefail

echo "=== stopping and locking mongod ==="
systemctl stop mongod 2>/dev/null || true
systemctl disable mongod 2>/dev/null || true
systemctl mask mongod 2>/dev/null || true

# Prevent polycopy health watcher from retrying (cron runs every 30m)
if [ -f /opt/polycopy/scripts/server_health_watch_research.sh ]; then
  sed -i 's/systemctl start mongod/# systemctl start mongod DISABLED/' \
    /opt/polycopy/scripts/server_health_watch_research.sh 2>/dev/null || true
fi

# Remove polycopy cron jobs that restart research + indirectly mongod
if crontab -l 2>/dev/null | grep -q polycopy; then
  crontab -l 2>/dev/null | grep -v polycopy | grep -v research-rollup | crontab - 2>/dev/null || true
  echo "removed polycopy entries from root crontab"
fi

rm -rf /var/log/mongodb/* 2>/dev/null || true

echo "mongod masked:" "$(systemctl is-enabled mongod 2>&1)"
echo "mongod active:" "$(systemctl is-active mongod 2>&1)"
crontab -l 2>/dev/null || echo "(empty crontab)"
