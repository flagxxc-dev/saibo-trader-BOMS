# Polymarket LIH Bot — C++ 交易核心

Polymarket **5m / 15m Up-Down** 市场（BTC / ETH / SOL）自动交易。主策略 **LIH（Leg-In Hedge）**：先买便宜边，再对冲到目标合价。

[![C++](https://img.shields.io/badge/C++-20-blue)](https://isocpp.org)
[![Polygon](https://img.shields.io/badge/Network-Polygon-purple)](https://polygon.technology)

> 遗留 **Dump Hedge** 已归档：[`archive/dh-only/`](archive/dh-only/)（设 `LIH_ENABLED=false` 可恢复 DH-only）。

---

## 策略逻辑（一局）

```
扫描盘口 → leg1（ask ≤ 0.45）→ 登记持仓 → 对冲（heavy_avg + light_ask ≤ 0.95）→ 结算 / 自动 redeem / 自动暂停
```

| 阶段 | 条件 | 说明 |
|------|------|------|
| **Leg1** | 某侧 ask ≤ `LIH_LEG1_MAX_PRICE`（默认 0.45） | 买 Up 或 Down 中更便宜的一侧 |
| **对冲** | `已买边均价 + 对面 ask ≤ LIH_TARGET_COMBINED`（默认 **0.95**） | 买对面配平 |
| **结算** | 市场到期 | `AUTO_REDEEM=true` 时自动链上 redeem |
| **暂停** | `LIH_PAUSE_AFTER_ROUND=true` | leg1+对冲完成或窗口结算后自动 PAUSE；Web 点「恢复」开下一局（session 清零） |

保守实盘默认：全局单槽（`LIH_ONE_SLOT_GLOBAL`）、每局最多 2 腿（leg1+对冲）、余额低于 $10 不开 leg1、窗口最后 30s 不开新 leg1。

### Leg1 / 对冲锁（不留尾巴）

`RiskManager` 维护 leg1 in-flight 与 rebalance 锁，防止同一 slot 重复开仓。Round 结束或异常路径会主动释放；此外 **`scrub_lih_inflight_locks`** 在主循环与 `LegInHedgeDetector::evaluate` 入口周期性清理：

- 对应 slot 已有 open 仓位 → 清除残留 leg1 in-flight
- 无仓位但锁超过 **120s** → 清除孤儿锁并打 warn 日志
- rebalance 锁在持仓不存在时立即清除

避免上一局 hedge/close 后误报 `leg1 in-flight` 或 `slot busy`，卡死下一窗口。

---

## 技术栈

| 层级 | 技术 | 作用 |
|------|------|------|
| **交易核心** | C++20 · CMake · Conan · Boost · spdlog · OpenSSL | 行情、LIH 检测、风控、下单编排；低延迟热路径 |
| **Python 桥接** | asyncio · python-dotenv · py-clob-client | 启动编排、HTTP/WS 仪表盘、`clob_live.py` 实盘下单与成交识别、链上 reconcile / redeem |
| **Web 仪表盘** | Next.js 16 · React 19 · Tailwind · Prisma/SQLite · NextAuth | 实时持仓/余额、暂停恢复、风控参数、历史 |
| **外部 API** | Polymarket CLOB WS/REST · Gamma · Binance WS（可选） | 订单簿、市场元数据、结算；Binance 仅仪表盘走势 |
| **部署** | Docker Compose · systemd · `remote_deploy.py` | 单/多实例、VPS SSH 部署与编译 |

**设计原则**：C++ 核心不直接暴露公网；前端只观测和下发配置/暂停，不实时驱动下单。

---

## 架构

```
frontend (Next.js :3001)
    │  WS :8080 / HTTP :8081
    ▼
dashboard_bridge.py + start_bot.py
    │  spawn
    ▼
trading-core (C++)
    ├── LegInHedgeDetector   # leg1 / 对冲信号
    ├── RiskManager          # 持仓、session、in-flight 锁、pause/resume
    ├── OrderRouter          # 调 clob_live 实盘 + pending 成交轮询
    └── StateStore           # 推 openPositions、余额、日志
         │
         ├── PolymarketFeed / GammaClient
         ├── clob_live.py → Polymarket CLOB
         └── redeem_positions.py → Polygon CTF redeem（AUTO_REDEEM）
```

Web 持仓来自 C++ 内存 → WebSocket。若成交未登记（如 `Bridge fill … x 0.00`），页面会显示 0；运行 `scripts/live_lih_reconcile.py` 或等 bridge 每 60s 自动 reconcile。

---

## 快速开始

```bash
cp .env.example .env
# 填写 POLYMARKET_PRIVATE_KEY / FUNDER / SIGNER（实盘）

./build.sh                 # Linux / VPS 编译 C++
python start_bot.py        # 启动 bridge + trading-core
```

**运行模式**：当前默认为 **实盘 LIVE**（`.env.example` 中 `PAPER_MODE=false`）。C++ 核心若读到 `PAPER_MODE=true` 会忽略并打 warn。上线前可用 `LIVE_LIH_DRY_RUN=true` 做只打日志不下单的 shadow 验证。

常用开关：

```bash
PAPER_MODE=false           # 实盘（默认）
LIH_ENABLED=true           # LIH 主策略
LIVE_LIH_DRY_RUN=false     # false = 真实 CLOB 下单
LIH_TARGET_COMBINED=0.95
LIH_PAUSE_AFTER_ROUND=true
AUTO_REDEEM=true           # 结算后自动 redeem（需 web3 + MATIC）
```

5m 市场 slug 格式（Gamma 探测）：`{asset}-updown-5m-{unix_ts}`，其中 `ts = (now // 300) * 300`。按币种开关：`DH_ENABLE_5M_BTC=true` 等。

Windows 编译见 [.env.example](.env.example) 同目录下的 `build.sh` 说明，或：

```powershell
pip install conan cmake ninja
conan profile detect --force
conan install trading-core --output-folder=build --build=missing -c tools.cmake.cmaketoolchain:generator=Ninja
cmake --preset conan-release -S trading-core
cmake --build build --config Release
```

**低内存 VPS（~1GB）**：`bash build-lowmem.sh`（单 ninja job、无 LTO）。

---

## 运维命令

| 任务 | 命令 |
|------|------|
| VPS 部署 | `python scripts/remote_deploy.py` |
| 部署 leg1 锁 + scrub 修复 | `python scripts/_deploy_leg1_lock_fix.py` |
| 部署 redeem 修复 | `python scripts/_deploy_redeem_leg1_fix.py` |
| 重启 bot（不重新编译） | `python scripts/_restart_bot_only.py` |
| 实盘前检查 | `python scripts/_preflight_live_test.py` |
| **跑一轮 live 验证** | `python scripts/_watch_test_round.py --enable-live --expect-assets btc` |
| 紧急停开仓 | `python scripts/_emergency_stop_entries.py` |
| 监控 VPS | `python scripts/live_monitor.py` |
| 链上持仓补录 | `python scripts/live_lih_reconcile.py` |
| 清理过期槽位 | `python scripts/prune_live_lih.py` |

`_watch_test_round.py` 默认 **watch-only**（不开仓）；必须显式传 `--enable-live` 才会设 `riskMax=1` 并 resume。Round 结束（CLOSED）或超时后自动设 `riskMax=0` 并 pause。

本地脚本通过 SSH 连 VPS（见 `scripts/remote_deploy.py` 中的 `HOST` / `PROJ`）；**C++ 改动需上传并在 VPS 上编译后才会生效**。

---

## 部署

| 方式 | 说明 |
|------|------|
| Docker 单实例 | `docker compose up -d --build` → 仪表盘 `http://<host>:3001` |
| 多实例 / 裸跑 / systemd | [deploy/README.md](deploy/README.md) |

```bash
cp .env.example .env
docker compose up -d --build
# bot WS :8080  API :8081  前端 :3001
```

---

## 目录速查

| 路径 | 说明 |
|------|------|
| `trading-core/src/signals/LegInHedgeDetector.*` | LIH 策略 |
| `trading-core/src/risk/RiskManager.*` | 持仓、风控、in-flight 锁、session、自动暂停 |
| `trading-core/src/exec/OrderRouter.*` | 实盘下单与 pending 成交 |
| `clob_live.py` / `clob_trades.py` | 实盘 CLOB 与成交查询 |
| `redeem_positions.py` | 链上 CTF redeem（neg-risk V2 + Data API 预检） |
| `dashboard_bridge.py` | WS 广播 + HTTP 控制 |
| `frontend/` | Web 仪表盘 |
| `.env.example` | 全部配置项说明 |
| `build-lowmem.sh` | 低内存 VPS 编译 |
| `scripts/_watch_test_round.py` | 单轮 live 测试与监控 |

---

## 免责声明

仅供学习与研究。预测市场交易有风险，请先用 shadow（`LIVE_LIH_DRY_RUN=true`）或小资金单轮验证，实盘自负盈亏。
