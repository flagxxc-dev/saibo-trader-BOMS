#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  POLYMARKET — by Genoshide | polymarket arbitrage script bot
#  scripts/install.sh — one-command installer for Linux / macOS / Git Bash
# ══════════════════════════════════════════════════════════════════════════════
#
#  Usage:
#    chmod +x scripts/install.sh
#    ./scripts/install.sh
#
#  What it does:
#    1. Checks Python 3.9+ is available
#    2. Creates a virtual environment in ./venv
#    3. Upgrades pip, setuptools, wheel
#    4. Installs all dependencies from requirements.txt
#    5. Copies .env.example → .env if .env doesn't exist
#    6. Runs the health check to verify everything is working
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Colour codes ─────────────────────────────────────────────────────────────
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

# ─── Change to repo root ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

echo ""
echo -e "${BLD}${CYN}  POLYMARKET${RST} by Genoshide  ·  polymarket arbitrage script bot"
echo -e "  Installer"
rule
echo ""

# ─── Step 1: Locate Python 3.9+ ───────────────────────────────────────────────
info "Checking Python version..."

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

[ -n "$PYTHON" ] || die "Python 3.9 or higher is required but not found on PATH.
  Install it from https://www.python.org/downloads/ and re-run this script."

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
success "Python $PY_VER found at $(command -v "$PYTHON")"

# ─── Step 2: Create virtual environment ───────────────────────────────────────
info "Creating virtual environment in ./venv ..."
if [ -d "venv" ]; then
    warn "venv/ already exists — skipping creation."
else
    "$PYTHON" -m venv venv
    success "Virtual environment created."
fi

# Activate
if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
    VENV_PY="venv/bin/python"
    VENV_PIP="venv/bin/pip"
elif [ -f "venv/Scripts/activate" ]; then
    # Git Bash on Windows
    # shellcheck disable=SC1091
    source venv/Scripts/activate
    VENV_PY="venv/Scripts/python"
    VENV_PIP="venv/Scripts/pip"
else
    die "Could not locate venv activation script."
fi

success "Virtual environment activated."

# ─── Step 3: Upgrade pip ──────────────────────────────────────────────────────
info "Upgrading pip, setuptools, wheel..."
"$VENV_PIP" install --upgrade pip setuptools wheel --quiet
success "pip upgraded."

# ─── Step 4: Install dependencies ─────────────────────────────────────────────
info "Installing dependencies from requirements.txt..."
"$VENV_PIP" install -r requirements.txt
success "Dependencies installed."

# ─── Step 5: Create .env ──────────────────────────────────────────────────────
rule
if [ -f ".env" ]; then
    warn ".env already exists — skipping. Edit it manually if needed."
else
    if [ -f ".env.example" ]; then
        cp .env.example .env
        success ".env created from .env.example"
        echo ""
        echo -e "  ${YEL}${BLD}ACTION REQUIRED:${RST} Open .env and fill in your credentials."
        echo -e "  Minimum for paper trading: nothing required (PAPER_MODE=true by default)."
        echo -e "  For live trading: set POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER."
    else
        warn ".env.example not found — you must create .env manually."
    fi
fi

# ─── Step 6: Health check ─────────────────────────────────────────────────────
rule
info "Running pre-flight health check..."
echo ""
"$VENV_PY" healthcheck.py || true   # health check prints its own pass/fail

# ─── Done ─────────────────────────────────────────────────────────────────────
rule
echo ""
echo -e "  ${BLD}${GRN}Installation complete.${RST}"
echo ""
echo -e "  ${BLD}Next steps:${RST}"
echo -e "    1. Edit ${CYN}.env${RST} with your settings (if not already done)"
echo -e "    2. Run ${CYN}./scripts/start.sh paper${RST}  — start in paper (simulation) mode"
echo -e "    3. Run ${CYN}./scripts/start.sh live${RST}   — start in live mode (real funds)"
echo -e "    4. Or use ${CYN}make paper${RST} / ${CYN}make live${RST} if you have GNU make"
echo ""
