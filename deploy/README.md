# 部署指南

本项目有两种部署方式：**Docker（推荐）** 和 **服务器裸跑**。  
网络能直连 Binance / Polymarket 即可，**不需要额外代理**。

每个 bot 进程的持仓、流水、交易历史都在**进程内存**里，互不共享。多开时只要隔离：**端口、.env、日志目录、（有 Web 时）前端数据库**。

---

## 架构一览

```
┌─────────────────┐     WebSocket      ┌──────────────────┐
│  Next.js 前端    │ ◄─────────────── │ dashboard_bridge │
│  :3001          │   ws://bot:8080   │  + trading-core  │
└─────────────────┘                    └──────────────────┘
                                              ▲
                                         读 .env 策略/钱包
```

- **Bot 包**：C++ `trading-core` + Python `dashboard_bridge.py`（WS 默认 8080）
- **Web 包**：`frontend/`（Next.js，通过 `BOT_WS_URL` 连 bot）
- **仅跑 bot、不要 Web**：只启动 bridge 即可，用 `cli_dashboard.py` 或自建监控连 `ws://IP:8080`

---

## 方式一：Docker 部署

### 1. 单实例（一台服务器一套 bot + 仪表盘）

```bash
cd /opt/polymarket-bot
cp .env.example .env          # 编辑策略、PAPER_MODE、钱包
docker compose up -d --build

docker compose ps
docker compose logs -f bot
```

| 服务 | 容器内端口 | 宿主机默认映射 |
|------|------------|----------------|
| bot（WS） | 8080 | 8080 |
| frontend | 3001 | 3001 |

浏览器访问：`http://服务器IP:3001`（默认账号 `admin` / `admin`）。

改 `.env` 后：`docker compose restart bot`（会重置纸面账本）。

---

### 2. 多实例 Docker（多开、数据不串）

**一实例 = 一个 Compose 项目名 + 独立端口 + 独立 bot.env + 独立 logs + 独立前端库。**

```bash
# 准备两个实例目录
cp -r deploy/instances/example deploy/instances/bot-a
cp -r deploy/instances/example deploy/instances/bot-b

# bot-a/compose.env → BOT_HTTP_PORT=8080, FRONTEND_HTTP_PORT=3001
# bot-b/compose.env → BOT_HTTP_PORT=8081, FRONTEND_HTTP_PORT=3002
# 各自编辑 bot.env（实盘必须不同钱包）

docker compose build   # 镜像只需构建一次

docker compose -p bot-a \
  --env-file deploy/instances/bot-a/compose.env \
  -f docker-compose.multi.yml up -d

docker compose -p bot-b \
  --env-file deploy/instances/bot-b/compose.env \
  -f docker-compose.multi.yml up -d
```

| 必须隔离 | 原因 |
|----------|------|
| `-p` 项目名 | 容器、网络、数据卷前缀 |
| `BOT_HTTP_PORT` / `FRONTEND_HTTP_PORT` | 宿主机端口不能重复 |
| `bot.env` | 策略与钱包 |
| `logs/` | 日志目录 |
| 前端 `frontend-db` 卷 | 登录会话 |
| 实盘 `POLYMARKET_PRIVATE_KEY` | 同钱包多进程会重复下单 |

---

### 3. Docker 镜像打包带到服务器

**在能构建的机器上：**

```bash
docker compose build
docker save polymarket-cpp-bot:latest polymarket-cpp-frontend:latest | gzip > polymarket-images.tar.gz
```

**在服务器上：**

```bash
docker load < polymarket-images.tar.gz
# 拷贝项目里的 docker-compose*.yml、deploy/、.env，无需再编译 C++
docker compose up -d
```

或使用私有镜像仓库 `docker push` / `docker pull`。

---

## 方式二：服务器裸跑（不用 Docker）

适合已有 Node/Python 环境、或想直接用 systemd 管进程的场景。

### 环境依赖

| 组件 | 版本建议 |
|------|----------|
| OS | Linux x86_64 |
| Python | 3.12+ |
| Node.js | 20+ |
| 构建 | gcc/clang、cmake、conan（见 `build.sh`） |

---

### 1. 编译并启动 Bot

```bash
cd /opt/polymarket-bot
cp .env.example .env
# 编辑 .env

# 编译 C++（首次较慢）
./build.sh

# Python 依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 纸面模式可跳过；实盘会写 POLY_API_KEY 等到 .env
python3 derive_and_update_keys.py

# 前台运行（推荐：自检 + 配置摘要 + bridge/core，终端可见输出）
python3 start_bot.py

# 仅自检（不启动 bot）
# python3 start_bot.py --preflight-only
# python3 start_bot.py --json-only          # 脚本用，只打 JSON

# 查看状态（bot 已在跑时）
# python3 status_bot.py --live

# 或直接跑 bridge（跳过 start_bot 包装）
# python3 dashboard_bridge.py
```

**后台运行示例：**

```bash
mkdir -p logs
nohup python3 start_bot.py >> logs/bridge.log 2>&1 &
# 查看状态
python3 status_bot.py --live
```

环境变量（可选）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `WS_HOST` | `0.0.0.0` | WS 监听地址 |
| `WS_PORT` | `8080` | WS 端口 |

验证：`curl` 不可用 WS，可用 `websocat ws://127.0.0.1:8080` 或看 `logs/bot.log`。

---

### 2. 启动 Web 仪表盘（裸跑）

Bot 必须先起来。另开终端：

```bash
cd /opt/polymarket-bot/frontend
npm ci
npx prisma generate
export DATABASE_URL="file:./prisma/data/dev.db"
npx prisma db push
npx prisma db seed

export BOT_WS_URL="ws://127.0.0.1:8080"
export PORT=3001
export NEXTAUTH_URL="http://你的服务器IP:3001"
export NEXTAUTH_SECRET="请换成随机长字符串"
export AUTH_TRUST_HOST=true

npm run build
npm run start
```

浏览器：`http://服务器IP:3001`。

---

### 3. 裸跑多实例

每个实例**单独目录**（或同目录不同 `.env` + 不同端口），例如：

**实例 A**（`/opt/bot-a`）：

```bash
cd /opt/bot-a
cp /opt/polymarket-bot/build/trading-core ./build/   # 或各自 build
cp bot.env .env
WS_PORT=8080 nohup python3 dashboard_bridge.py >> logs/bridge.log 2>&1 &
```

**实例 A 前端**：

```bash
BOT_WS_URL=ws://127.0.0.1:8080 PORT=3001 DATABASE_URL=file:./prisma/data-a/dev.db npm run start
```

**实例 B**：`WS_PORT=8081`，前端 `PORT=3002`，`BOT_WS_URL=ws://127.0.0.1:8081`，**另一份** `bot.env` 与 prisma 目录。

---

### 4. systemd 托管（裸跑 Bot 示例）

见 `deploy/systemd/polymarket-bot@.service.example`，可按实例名启用：

```bash
sudo cp deploy/systemd/polymarket-bot@.service.example /etc/systemd/system/polymarket-bot@.service
# 编辑 WorkingDirectory、EnvironmentFile 指向 deploy/instances/bot-a/
sudo systemctl enable --now polymarket-bot@bot-a
```

---

## 端口与安全建议

| 端口 | 服务 | 建议 |
|------|------|------|
| 8080+ | bot WebSocket | 仅本机或内网；前端通过 `BOT_WS_URL` 访问 |
| 3001+ | Web 仪表盘 | 可对公网，建议 Nginx + HTTPS + 强密码 |

防火墙示例：只放行 `3001`，不放行 `8080` 到公网。

---

## 纸面 vs 实盘

| | 纸面 `PAPER_MODE=true` | 实盘 `PAPER_MODE=false` |
|--|------------------------|-------------------------|
| 钱包 | 可不填真实私钥 | 必须填 `POLYMARKET_PRIVATE_KEY` |
| 余额 | 内存模拟，可持久化 JSON | 链上/SDK 真实余额 |
| 多开 | 可同参数测策略 | **必须不同钱包** |
| 到期 | 结构结算（纸面账本） | 结构结算 + 可选链上 redeem（`AUTO_REDEEM`） |

**上实盘前必读：** [LIVE_READINESS.md](./LIVE_READINESS.md)

---

## 常用命令速查

**Docker 单实例**

```bash
docker compose up -d
docker compose logs -f bot
docker compose restart bot
docker compose down
```

**Docker 多实例**

```bash
docker compose -p bot-a -f docker-compose.multi.yml --env-file deploy/instances/bot-a/compose.env logs -f bot
docker compose -p bot-a -f docker-compose.multi.yml down
```

**裸跑**

```bash
./build.sh && python3 dashboard_bridge.py          # bot
cd frontend && npm run build && npm run start      # web
```

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `docker-compose.yml` | 单实例 Docker |
| `docker-compose.multi.yml` | 多实例 Docker 模板 |
| `deploy/instances/*/compose.env` | 实例端口、NEXTAUTH |
| `deploy/instances/*/bot.env` | 策略与钱包（挂载进容器或裸跑 `.env`） |
| `build.sh` | 裸跑编译 C++ |
| `dashboard_bridge.py` | Bot 入口（含 WS 服务） |
