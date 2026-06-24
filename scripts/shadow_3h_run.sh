#!/usr/bin/env bash
# Shadow observation run (default 3h). Same as shadow_1h_run.sh with separate log/report paths.
set -uo pipefail
ROOT="/chuan/saibo-trader-trend"
cd "$ROOT"
export ROOT
export PY="$ROOT/.venv/bin/python3"
export LOG="$ROOT/logs/shadow_3h_monitor.log"
export REPORT="$ROOT/logs/shadow_3h_report.txt"
DURATION_SEC="${1:-10800}"
INTERVAL_SEC="${2:-600}"

exec bash "$ROOT/scripts/shadow_1h_run.sh" "$DURATION_SEC" "$INTERVAL_SEC"
