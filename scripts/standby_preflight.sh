#!/usr/bin/env bash
# Local standby preflight — no bot start, no heavy build.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python3"
BIN="${ROOT}/build/trading-core"
BIN_TS=$(stat -c %Y "$BIN" 2>/dev/null || echo 0)
FAIL=0
WARN=0

pass() { echo "  [OK]   $*"; }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL + 1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN + 1)); }

section() { echo; echo "=== $* ==="; }

section "1. 目录与二进制"
test -f "$BIN" && pass "binary exists ($(stat -c '%s bytes %y' "$BIN"))" || fail "missing build/trading-core"
test -x "$BIN" && pass "binary executable" || fail "binary not executable"
test -f "$ROOT/.env" && pass ".env exists" || fail "missing .env"
test -d "$ROOT/logs" && pass "logs/ exists" || { mkdir -p "$ROOT/logs"; warn "created logs/"; }
test -w "$ROOT/logs" && pass "logs/ writable" || fail "logs/ not writable"

section "2. 二进制依赖"
if ldd "$BIN" 2>/dev/null | grep -q 'not found'; then
  ldd "$BIN" 2>/dev/null | grep 'not found' | while read -r line; do fail "missing lib: $line"; done
else
  pass "all shared libraries resolved"
fi

section "3. 二进制特性标记 (strings)"
for needle in "CLOB REST" "LIVE LIH SHADOW" "shadow_lih_pnl" "get_shadow_lih_pnl" "Book-aware exec"; do
  if strings "$BIN" 2>/dev/null | grep -Fq "$needle"; then
    pass "strings: $needle"
  else
    warn "strings missing: $needle"
  fi
done
if strings "$BIN" 2>/dev/null | grep -Fq "FATAL"; then
  pass "strings: FATAL (wallet gate present)"
fi
HAS_SHADOW_PNL=false
if strings "$BIN" 2>/dev/null | grep -Fq "get_shadow_lih_pnl"; then HAS_SHADOW_PNL=true; fi

section "4. 源码完整性 (git)"
MISSING=0
for f in \
  trading-core/src/risk/RiskManager.cpp \
  trading-core/src/signals/LegInHedgeDetector.cpp \
  trading-core/src/exec/OrderRouter.cpp \
  trading-core/src/state/StateStore.cpp \
  trading-core/src/main.cpp \
  dashboard_bridge.py \
  start_bot.py \
  bot_config.py; do
  if test -f "$ROOT/$f"; then
    pass "source: $f"
  else
    fail "missing source: $f"
    MISSING=$((MISSING + 1))
  fi
done
if $HAS_SHADOW_PNL; then
  pass "binary build includes shadow PnL CSV (Jun22 build)"
elif git -C "$ROOT" diff --quiet -- trading-core/src/risk/RiskManager.cpp 2>/dev/null; then
  pass "RiskManager.cpp matches git HEAD"
else
  warn "RiskManager.cpp differs from git"
fi
if test -f "$ROOT/trading-core/src/risk/RiskManager.cpp"; then
  SRC_TS=$(stat -c %Y "$ROOT/trading-core/src/risk/RiskManager.cpp")
  if [ "$BIN_TS" -lt "$SRC_TS" ] && ! $HAS_SHADOW_PNL; then
    warn "source newer than binary — rebuild recommended"
  fi
fi

section "5. Python 环境"
test -x "$PY" && pass "venv python" || fail "missing .venv/bin/python3"
"$PY" -c "import dotenv, websockets, aiohttp" 2>/dev/null && pass "core python deps" || fail "python deps missing (pip install -r requirements.txt)"

section "6. 钱包与模式"
if "$PY" "$ROOT/check_wallet_config.py" 2>&1; then
  pass "check_wallet_config.py"
else
  warn "check_wallet_config.py reported issues (expected if placeholder keys)"
fi

section "7. 融合 .env 关键项"
# shellcheck disable=SC1091
set -a; source "$ROOT/.env"; set +a
test "${LIVE_LIH_DRY_RUN:-true}" = "true" && pass "LIVE_LIH_DRY_RUN=true (shadow)" || warn "LIVE_LIH_DRY_RUN=false — live orders enabled"
test "${LIH_ENABLED:-false}" = "true" && pass "LIH_ENABLED=true" || fail "LIH_ENABLED not true"
test "${DH_BOOK_AWARE_DETECT:-false}" = "true" && pass "DH_BOOK_AWARE_DETECT=true" || warn "DH_BOOK_AWARE_DETECT=false"
test "${LIH_LEG1_MODE:-}" = "trend" && pass "LIH_LEG1_MODE=trend" || warn "LIH_LEG1_MODE=${LIH_LEG1_MODE:-unset}"
test "${LIH_MAX_USDC_PER_SLOT:-1}" = "0" && pass "LIH_MAX_USDC_PER_SLOT=0 (no fixed cap)" || warn "LIH_MAX_USDC_PER_SLOT=${LIH_MAX_USDC_PER_SLOT:-unset}"
grep -q '^LIH_ENDGAME_RESUME_HEDGE_ASK=0.25' "$ROOT/.env" && pass "fusion endgame RESUME=0.25" || warn "endgame params may not be fusion v1"

section "8. 网络只读探测 (CLOB/Gamma/Geo)"
if curl -sf --max-time 8 "https://polymarket.com/api/geoblock" | grep -q blocked; then
  GEO=$(curl -sf --max-time 8 "https://polymarket.com/api/geoblock")
  warn "geoblock: $GEO (shadow read OK; live orders blocked from this region)"
else
  pass "geoblock API reachable"
fi
if curl -sf --max-time 8 "https://gamma-api.polymarket.com/markets?limit=1" | grep -q conditionId; then
  pass "Gamma API OK"
else
  fail "Gamma API unreachable"
fi
if curl -sf --max-time 8 "https://clob.polymarket.com/time" | grep -q .; then
  pass "CLOB REST OK"
else
  fail "CLOB REST unreachable"
fi

section "9. 二进制冒烟 (5s, 预期 FATAL: placeholder key)"
set +e
stdbuf -oL -eL timeout 5 "$BIN" > /tmp/trading_core_smoke.txt 2>&1
SMOKE=$?
set -e
if grep -qE 'FATAL|Starting Core|Book-aware|Strategy:|LIH' /tmp/trading_core_smoke.txt 2>/dev/null; then
  pass "binary produces startup output (exit=$SMOKE)"
  grep -E 'FATAL|Starting Core|Book-aware|Strategy:|LIH|Mode' /tmp/trading_core_smoke.txt | head -6 | sed 's/^/         /'
  if grep -q FATAL /tmp/trading_core_smoke.txt; then
    warn "FATAL expected until real POLYMARKET_PRIVATE_KEY is set"
  fi
else
  warn "no startup lines captured (exit=$SMOKE, $(wc -c < /tmp/trading_core_smoke.txt) bytes)"
  head -3 /tmp/trading_core_smoke.txt 2>/dev/null | sed 's/^/         /'
fi

section "10. 进程状态"
if pgrep -af 'start_bot|trading-core' >/dev/null 2>&1; then
  warn "bot/core already running:"
  pgrep -af 'start_bot|trading-core' | sed 's/^/         /'
else
  pass "no bot/core process (standby)"
fi

section "SUMMARY"
echo "  failures: $FAIL | warnings: $WARN"
if [ "$FAIL" -gt 0 ]; then
  echo "  → fix FAIL items before starting bot"
  exit 1
fi
echo "  → standby preflight passed (warnings OK for shadow prep)"
exit 0
