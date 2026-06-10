#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  POLYMARKET — by Genoshide | polymarket arbitrage script bot
#  scripts/start.sh — launcher for Linux / macOS / Git Bash
# ══════════════════════════════════════════════════════════════════════════════
#
#  Usage:
#    ./scripts/start.sh          # use PAPER_MODE from .env
#    ./scripts/start.sh paper    # force paper (simulation) mode
#    ./scripts/start.sh live     # force live mode (real funds — 5s abort window)
#    ./scripts/start.sh health   # run health check only
#
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
CYN='\033[0;36m'
BLD='\033[1m'
RST='\033[0m'

info()    { echo -e "${CYN}  ▶${RST}  $*"; }
success() { echo -e "${GRN}  ✓${RST}  $*"; }
warn()    { echo -e "${YEL}  ⚠${RST}  $*"; }
error()   { echo -e "${RED}  ✗${RST}  $*" >&2; }
die()     { error "$*"; exit 1; }
rule()    { echo -e "${BLD}${CYN}──────────────────────────────────────────────────────────${RST}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

MODE="${1:-}"

# ─── Locate Python in venv ────────────────────────────────────────────────────
if [ -f "venv/bin/python" ]; then
    VENV_PY="venv/bin/python"
elif [ -f "venv/Scripts/python" ]; then
    VENV_PY="venv/Scripts/python"
elif [ -f "venv/Scripts/python.exe" ]; then
    VENV_PY="venv/Scripts/python.exe"
else
    die "Virtual environment not found. Run ./scripts/install.sh first."
fi

# ─── Verify .env exists ───────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    warn ".env not found — copying .env.example → .env"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "  ${YEL}Edit .env before running the bot in live mode.${RST}"
    else
        die ".env.example not found. Cannot create .env automatically."
    fi
fi

# ─── Dispatch ─────────────────────────────────────────────────────────────────
case "$MODE" in
    health)
        info "Running health check..."
        "$VENV_PY" healthcheck.py
        ;;

    paper)
        echo ""
        echo -e "  ${BLD}${CYN}POLYMARKET${RST}  starting in ${BLD}PAPER${RST} mode  (simulation — no real funds)"
        rule
        "$VENV_PY" main.py --paper
        ;;

    live)
        echo ""
        echo -e "  ${BLD}${CYN}POLYMARKET${RST}  starting in ${BLD}${RED}LIVE${RST} mode"
        rule
        echo -e "  ${YEL}${BLD}WARNING:${RST} Real funds will be used."
        echo -e "  ${YEL}Press Ctrl+C within 5 seconds to abort.${RST}"
        echo ""
        for i in 5 4 3 2 1; do
            echo -ne "  Starting in ${BLD}$i${RST}...\r"
            sleep 1
        done
        echo ""
        "$VENV_PY" main.py --live
        ;;

    "")
        echo ""
        echo -e "  ${BLD}${CYN}POLYMARKET${RST}  starting (mode from .env)"
        rule
        "$VENV_PY" main.py
        ;;

    *)
        die "Unknown mode: '$MODE'. Use: paper | live | health"
        ;;
esac
