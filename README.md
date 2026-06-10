# POLYMARKET ARBITRAGE BOT — C++ HIGH-PERFORMANCE CORE

> **Polymarket resolves every 5 minutes. The oracle lags 2.7 seconds behind Binance. This bot lives in that gap — now with C++ execution speeds.**

This is a high-performance port of the original Python arbitrage bot, rebuilt from the ground up in C++20 for sub-millisecond execution. It runs two independent arbitrage strategies — latency arb and structural dump-hedge — protected by adaptive Kelly sizing and a real-time terminal dashboard.

[![C++](https://img.shields.io/badge/C++-20-blue)](https://isocpp.org)
[![Polygon](https://img.shields.io/badge/Network-Polygon_Mainnet-purple)](https://polygon.technology)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## What This Bot Does

The bot watches Polymarket binary prediction markets (e.g. "Will BTC be higher in 5 minutes?") and places trades when it detects a statistical edge. By utilizing a zero-allocation hot path and circular history buffers in C++, it minimizes the "time-to-fill" after a price move is detected.

- **Latency Arb** — exploits the ~2.7-second lag between Binance price moves and Polymarket's oracle update
- **Dump Hedge** — buys both YES and NO simultaneously when their combined price falls below $1.00, locking in a guaranteed structural profit

---

## Core Performance Features

- **Zero-Allocation Hot Path**: Signal detection and fair value calculation use pre-allocated state to minimize GC/latency spikes.
- **Circular History Buffers**: Maintains 100 seconds of BTC/ETH price ticks for precise history-aware lookups (e.g., querying price exactly 2.7s ago).
- **History-Aware Sigmoid**: Fair value is calculated using the *actual* price-to-beat from history, not just current price differentials.
- **Adaptive Kelly Sizer**: Dynamically adjusts bet size based on balance and real-time win-rate performance.

---

## Signal Validation Filters

Every potential latency arb signal passes through five sequential filters. All must pass before a trade fires.

1. **MIN PRICE MOVE**: `abs(price_now − price_2.7s_ago) > min_price_move`
2. **ENTRY ZONE**: `0.38 ≤ current_token_price ≤ 0.62`
3. **FAIR VALUE STRENGTH**: `abs(fair_value − 0.50) ≥ 0.05`
4. **MINIMUM EDGE**: `fair_value − token_price ≥ 0.05`
5. **TIMING WINDOW**: Avoids entries in the final 20% of the market window.

---

## Project Structure

```
trading-core/
├── src/
│   ├── main.cpp                # Core orchestrator & event loop
│   ├── signals/                # LatencyArb and DumpHedge detectors
│   ├── risk/                   # KellySizer and RiskManager
│   ├── feeds/                  # High-frequency Binance WebSocket feed
│   ├── state/                  # StateStore (circular buffers, thread-safe cache)
│   └── networking/             # WebSocket server for dashboard broadcast
├── build/                      # Compiled high-performance binaries
├── build.sh                    # CMake-based build script
└── start.sh                    # Process manager (starts Core + Dashboard)

cli_dashboard.py                # Premium Rich-based terminal monitoring
```

---

### 1. Build the C++ Core
Ensure you have `cmake`, `ninja`, and `conan` installed (or run `./build.sh` on Linux to auto-install).
On Windows:
```powershell
# In PowerShell as Administrator
pip install conan cmake ninja
conan profile detect --force
conan install trading-core --output-folder=build --build=missing -c tools.cmake.cmaketoolchain:generator=Ninja
cmake --preset conan-release -S trading-core
cmake --build build --config Release
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your Polymarket credentials.
```bash
cp .env.example .env
```

### 3. Launch
On Windows:
```powershell
./start_windows.ps1
```
On Linux:
```bash
./start.sh
```

---

## Disclaimer

This software is provided for educational and experimental purposes. Prediction market trading involves significant financial risk. Past performance does not guarantee future results. You are solely responsible for any financial losses. Always validate with paper trading before deploying real capital.

---

## 部署

| 方式 | 说明 | 文档 |
|------|------|------|
| **Docker 单实例** | `docker compose up -d --build`，适合大多数服务器 | 下文 |
| **Docker 多实例** | 多开 bot，端口/配置/数据隔离 | [deploy/README.md](deploy/README.md) |
| **服务器裸跑** | 不用 Docker，systemd 管进程 | [deploy/README.md](deploy/README.md) |

### Docker 单实例（最快）

```bash
cp .env.example .env
docker compose up -d --build
# 仪表盘 http://<服务器IP>:3001  默认 admin/admin
# bot WebSocket 映射到宿主机 8080
```

多实例、镜像打包、裸跑编译与 systemd：见 **[deploy/README.md](deploy/README.md)**。
