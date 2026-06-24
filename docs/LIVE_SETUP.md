# 实盘配置与运行手册

> 本手册基于一次真实的从零配置过程整理,覆盖:钱包配置、API 凭据派生、资金准备、编译、启动、监控、回滚与排错。
> 策略为 **DH-only**(结构对冲):YES+NO 合价 < $1 时双边买入,锁定结构性利润,持有至窗口到期。
> 配套文档:[deploy/LIVE_READINESS.md](../deploy/LIVE_READINESS.md)(实盘就绪清单)、[CLAUDE.md](../CLAUDE.md)(架构说明)。

---

## 0. 前置条件

- macOS / Linux,Python 3.10+,`cmake`/`conan`/`ninja`(没有也行,`build.sh` 会自动装进 venv)
- 一个 Polymarket 账户(网页注册即可)
- 能直连 `clob.polymarket.com`、`polygon-rpc.com`、Binance(可选)

---

## 1. 理解两个地址(最容易配错的地方)

Polymarket 网页注册的账户是**代理钱包**结构,涉及两个不同的地址:

| 角色 | 哪里找 | 填到哪 |
|------|--------|--------|
| **代理钱包(FUNDER)** | 网站「个人资料」页显示的地址(USDC/pUSD 记在这里) | `POLYMARKET_FUNDER` |
| **签名 EOA(SIGNER)** | 由网站「导出私钥」得到的私钥推导而来,**和上面不是同一个地址** | `POLYMARKET_SIGNER` |

从私钥推导 SIGNER 地址(本地计算,不联网):

```bash
.venv/bin/python -c "
from eth_account import Account
print(Account.from_key('0x你的私钥').address)"
```

⚠️ 两个地址不同 → 代理模式(signature_type=1),这是网页账户的正常情况。
⚠️ **`POLYMARKET_SIGNER` 不能留空**:留空时 C++ 会默认 signer = funder,签名全部无效。
⚠️ 网站「Relayer API 密钥」页面创建的 key 对本项目**没有用**,bot 不读它;所需的 CLOB 凭据由第 3 步自动派生。

---

## 2. 创建环境与 `.env`

```bash
cd <仓库根目录>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
```

编辑 `.env`,实盘必改 4 项:

```env
POLYMARKET_FUNDER=0x<网站显示的代理地址>
POLYMARKET_SIGNER=0x<私钥推导出的EOA地址>
POLYMARKET_PRIVATE_KEY=0x<网站导出的私钥,64位hex>
AUTO_REDEEM=true            # 见第 4 节 gas 说明
```

其余参数(风控、DH 阈值)默认值可直接用,含义见 `.env.example` 注释。首跑建议保持默认。

`.env` 已在 `.gitignore` 中,不会被提交;但仍注意**不要把私钥粘贴到任何聊天/工单/截图里**。

---

## 3. 派生 CLOB L2 API 凭据

实盘硬性要求 `POLY_API_KEY / POLY_API_SECRET / POLY_PASSPHRASE` 三件套,缺失则启动直接 FATAL。不需要手填,跑:

```bash
.venv/bin/python derive_and_update_keys.py
```

成功输出 `AUTHENTICATION SUCCESSFUL! API keys saved to .env`,三件套自动回写 `.env`。
(开头若出现一行 `status=400 ... Could not create api key` 是正常回退:创建失败转为派生已有 key。)

这一步本身完成了一次真实签名鉴权——成功即证明私钥与代理钱包配置有效。

然后校验:

```bash
.venv/bin/python check_wallet_config.py
```

期望:`Verdict: CONFIG LOOKS OK`,且钱包模式识别为 `PROXY (signature_type=1)`、`Private key derives EOA` 与 SIGNER 一致。

> 已知误报:`check_wallet_config.py` 的链上余额检查可能显示 `$0`(它走的公共 RPC 不可靠且不查 pUSD 全部形态)。**以下一步的 `fetch_balance.py` 为准**,那才是 bot 实盘真正调用的脚本。
> 鉴权验证：运行 <code>python derive_and_update_keys.py</code> 与 <code>python live_preflight.py</code>。

---

## 4. 资金准备(链上)

| 资金 | 放哪 | 多少 | 用途 |
|------|------|------|------|
| USDC / pUSD | 代理钱包(网页充值即可) | 首跑 $20–50 | 交易本金 |
| POL (MATIC) | **EOA 地址**(交易所提现选 Polygon 链) | ~0.5 POL | `AUTO_REDEEM` 链上赎回的 gas |

验证 bot 能看到本金:

```bash
.venv/bin/python fetch_balance.py
# 期望输出: [fetch_balance] on-chain pUSD: $21.95... 之类的非零数字
```

**EOA 没有 POL 也能交易**,只是到期自动赎回会 `REDEEM FAIL`;赢的钱不会丢,去 Polymarket 网页端手动 Claim(网页走官方 gasless relayer,不耗 gas)。小资金试跑阶段手动 Claim 完全够用,也可以直接设 `AUTO_REDEEM=false`。

---

## 5. 编译 C++ 核心

```bash
bash build.sh        # 注意:直接 ./build.sh 可能因无执行权限报 126
```

首次构建会拉取 Conan 依赖并从源码编译(boost、spdlog、secp256k1 等),需要几分钟;期间 autotools 的 warning 无害。产物:`build/trading-core`。增量构建很快,需要彻底重来时:`rm -rf build/CMakeCache.txt build/CMakeFiles`。

---

## 6. 启动与监控

### 方式 A:前台一条龙(适合首跑盯盘)

```bash
source .venv/bin/activate
./start.sh           # 或 bash start.sh
```

它会:派生 key → 后台启动 `dashboard_bridge.py`(拉起 trading-core,开 ws://0.0.0.0:8080)→ 前台打开终端仪表盘。关闭仪表盘时会连带停掉 bot。

### 方式 B:后台运行 + 随开随关的观察窗口

```bash
# 启动(bridge 负责拉起 trading-core)
nohup .venv/bin/python dashboard_bridge.py > bridge.log 2>&1 &

# 任何时候想看盘(关掉不影响 bot):
.venv/bin/python cli_dashboard.py

# 停止
pkill -f dashboard_bridge.py; pkill -f trading-core
```

### 方式 C:Web 界面(可选)

`frontend/` 下 `npm install && npx prisma db push && npx tsx prisma/seed.ts && npm run dev`,访问 `http://localhost:3001`(默认 admin/admin,经 `BOT_WS_URL` 连 8080)。对外暴露前必须改 `NEXTAUTH_SECRET` 和 admin 密码。

### Docker 方式的一个坑

`docker-compose.yml` 把 `.env` 挂成**只读**,容器内无法回写 API key——必须先在宿主机完成第 3 步再 `docker compose up -d --build`。

---

## 7. 首笔交易人工核对(必做)

本项目**没有自动化测试**,C++ EIP-712 签名从未与官方 SDK 做过向量比对,首单必须人工验证:

1. 盯日志:`tail -f bot.log`,关注关键词
   - `[LIVE DH] OPENED` — 开仓成功
   - `SETTLED ... RESOLVED` — 到期结算
   - `REDEEM OK` / `REDEEM FAIL` — 链上赎回结果
2. 开仓后立即到 Polymarket 网页「交易」页核对:持仓存在、两腿数量与价格和日志一致
3. 到期后在 [Polygonscan](https://polygonscan.com/) 查赎回交易;若 `REDEEM FAIL` 或金额为 0,网页端手动 Claim
4. 核对网页现金余额与仪表盘余额一致(实盘余额每 60s 自动同步)

> 代理钱包账户的已知风险:自动赎回交易由 EOA 发出,而持仓代币记在代理地址名下,赎回可能不生效。资金不会丢失,但请按上面第 3 条人工确认,必要时手动 Claim。

---

## 8. 回滚 shadow / 紧急停止

```bash
# 紧急停止
pkill -f dashboard_bridge.py; pkill -f trading-core

# 切回 shadow（不下单）:.env 改一行
LIVE_LIH_DRY_RUN=
# 然后重启
```

内置熔断(无需配置即生效):日亏 20% 当日停手、总回撤 40% 永久停机(`RISK_DAILY_LOSS_LIMIT` / `RISK_TOTAL_DRAWDOWN_KILL`)。

---

## 9. 排错速查

| 现象 | 原因与处理 |
|------|-----------|
| 启动即 FATAL `POLY_API_KEY is missing` | 没跑第 3 步派生脚本 |
| 启动即 FATAL `requires a valid POLYMARKET_PRIVATE_KEY` | 私钥还是占位符,或格式不对(需 0x+64 hex) |
| `./build.sh` 报 permission denied (126) | 用 `bash build.sh` |
| `check_wallet_config.py` 报余额 $0 但网页有钱 | 该脚本 RPC 检查不可靠;以 `fetch_balance.py` 为准 |
| 派生/预检失败 | 检查 `.env` 私钥与 funder；运行 `derive_and_update_keys.py` |
| 下单报 `order_version_mismatch` | neg-risk 市场用错 verifying contract(代码已处理,出现则提 issue) |
| `REDEEM FAIL` | EOA 无 POL gas / RPC 超时 / 市场未 finalize / 代理账户赎回限制 → 网页手动 Claim |
| DH 一直不开仓 | 正常:需 YES+NO ≤ `DH_SUM_TARGET` 且折价覆盖手续费,行情平静时一天可能没几单;另查冷却期、同资产已有持仓、剩余时间 < 60s |
| 仪表盘连不上 | bridge 没起来或 8080 被占;`lsof -i :8080` 检查 |

---

## 10. 一页纸命令总结

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && vi .env        # 钱包 4 项(见第 1-2 节)
.venv/bin/python derive_and_update_keys.py
.venv/bin/python check_wallet_config.py
.venv/bin/python fetch_balance.py      # 确认非零
bash build.sh
nohup .venv/bin/python dashboard_bridge.py > bridge.log 2>&1 &
.venv/bin/python cli_dashboard.py      # 观察窗口,随开随关
tail -f bot.log                        # 首单人工核对,见第 7 节
```
