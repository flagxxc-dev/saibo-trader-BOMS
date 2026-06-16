#!/bin/bash
# Detached bot start (survives SSH session close)
set -e
cd /opt/polymarket-bot
# trading-core popen("python3 fetch_balance.py") must hit project venv + deps
export PATH="$(pwd)/.venv/bin:$PATH"
mkdir -p logs
pkill -f 'start_bot.py' 2>/dev/null || true
pkill -f 'dashboard_bridge.py' 2>/dev/null || true
pkill -f '/opt/polymarket-bot/build/trading-core' 2>/dev/null || true
sleep 2
# Skip preflight/prelive on restart — checks run on deploy or manually.
export START_SKIP_PRELIVE="${START_SKIP_PRELIVE:-1}"
setsid -f -- .venv/bin/python -u start_bot.py --skip-preflight >> logs/bridge.log 2>&1
sleep 3
pgrep -af 'start_bot|trading-core' || { echo "FAILED to start"; tail -20 logs/bridge.log; exit 1; }
