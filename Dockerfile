# ══════════════════════════════════════════════════════════════════════════════
#  Polymarket Arbitrage Bot — C++ core + Python bridge
# ══════════════════════════════════════════════════════════════════════════════

FROM python:3.12-slim-bookworm AS cpp-builder

ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG http_proxy=
ARG https_proxy=
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY=*

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir conan==2.3.2

WORKDIR /src
COPY trading-core/conanfile.txt trading-core/CMakeLists.txt ./trading-core/

WORKDIR /src/trading-core
# Split layers: Conan deps (slow download/build) cached unless conanfile changes.
RUN conan profile detect --force \
 && conan install . --output-folder=build --build=missing -s compiler.cppstd=20

COPY trading-core/src ./src
# BuildKit may inject host proxy (127.0.0.1) — clear for git FetchContent (secp256k1).
RUN export HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= ALL_PROXY= all_proxy= NO_PROXY='*' \
 && git config --global http.proxy "" \
 && git config --global https.proxy "" \
 && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=build/conan_toolchain.cmake \
 && cmake --build build --target trading-core -j$(nproc)

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG http_proxy=
ARG https_proxy=
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY=*

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini libssl3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard_bridge.py bot_config.py derive_and_update_keys.py fetch_balance.py redeem_positions.py cli_dashboard.py live_preflight.py polymarket_fees.py start_bot.py status_bot.py ./
COPY --from=cpp-builder /src/trading-core/build/trading-core ./build/trading-core
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh \
 && chmod +x /entrypoint.sh \
 && mkdir -p /app/logs

EXPOSE 8080 8081
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',8080)); s.close()"

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/entrypoint.sh"]
