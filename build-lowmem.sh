#!/bin/bash
# Low-memory C++ build for ~1GB VPS: single ninja job, no LTO, -O2.
# Usage: bash build-lowmem.sh   (from repo root)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> build-lowmem: killing stale compile processes..."
self=$$
for pid in $(pgrep -f 'build\.sh|cmake --build|/ninja|lto1|cc1plus' 2>/dev/null || true); do
  [ "$pid" = "$self" ] && continue
  kill -9 "$pid" 2>/dev/null || true
done
sleep 1

if ! command -v conan &>/dev/null || ! command -v cmake &>/dev/null; then
  if [ ! -d ".venv" ]; then
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip3 install -q conan cmake ninja
else
  if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
fi

conan profile detect --force || true
sed -i.bak 's/compiler.version=21/compiler.version=15.0/g' ~/.conan2/profiles/default 2>/dev/null || true

export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-1}"
export NINJAFLAGS="${NINJAFLAGS:--j1}"

echo "==> build-lowmem: conan install (parallel=$CMAKE_BUILD_PARALLEL_LEVEL)..."
conan install trading-core --output-folder=build --build=missing \
  -c tools.cmake.cmaketoolchain:generator=Ninja

echo "==> build-lowmem: cmake configure TRADING_CORE_LOWMEM=ON..."
cmake --preset conan-release -S trading-core -DTRADING_CORE_LOWMEM=ON

echo "==> build-lowmem: ninja build (low RAM)..."
cmake --build build --config Release --parallel "${CMAKE_BUILD_PARALLEL_LEVEL}"

echo "==> build-lowmem OK: build/trading-core"
