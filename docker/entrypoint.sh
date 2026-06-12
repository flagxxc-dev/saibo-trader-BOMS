#!/bin/bash
set -euo pipefail

# Docker Desktop injects 127.0.0.1:10809 — that is the container itself, not the host.
# Remap to host.docker.internal so Python tools can use the host proxy when needed.
# C++ core connects directly; Polymarket is excluded so DH/LA PM feeds stay direct.
_fix_proxy() {
  local v="$1"
  [[ -z "$v" ]] && return
  v="${v//127.0.0.1/host.docker.internal}"
  v="${v//localhost/host.docker.internal}"
  echo "$v"
}
if [[ -n "${HTTP_PROXY:-}" || -n "${http_proxy:-}" ]]; then
  _p="$(_fix_proxy "${HTTP_PROXY:-$http_proxy}")"
  export HTTP_PROXY="$_p" http_proxy="$_p"
  export HTTPS_PROXY="$_p" https_proxy="$_p"
fi
export NO_PROXY="${NO_PROXY:-},gamma-api.polymarket.com,clob.polymarket.com,ws-subscriptions-clob.polymarket.com,polymarket.com"
export no_proxy="$NO_PROXY"

cd /app

if [[ ! -f .env ]]; then
  echo "[entrypoint] .env not found — writing from container environment"
  env | grep -E '^(PAPER_MODE|POLYMARKET_|PAPER_|RISK_|FEE_|TRAILING_|NEAR_|TAKE_PROFIT_|STOP_LOSS_|POSITION_|ENTRY_)' > .env || true
fi

echo "[entrypoint] Deriving L2 API keys (skipped in paper mode)..."
python3 derive_and_update_keys.py || true

echo "[entrypoint] Startup preflight (wallet / EIP-712 / fee model)..."
python3 live_preflight.py || true

echo "[entrypoint] Starting dashboard bridge + C++ trading core..."
exec python3 dashboard_bridge.py
