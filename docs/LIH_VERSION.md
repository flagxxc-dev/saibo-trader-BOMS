# LIH 策略版本留档

本文档记录 **Leg-In Hedge（LIH）** 实盘栈的功能版本、配置基线与待开发项。  
Git 以 commit hash 为准；语义版本便于口头对齐。

---

## 当前基线：`v0.9.0-lih-baseline`

| 项 | 值 |
|---|---|
| **Git** | `0268601` — `feat: LIH leg1 Binance trend filter and 60s force-balance window` |
| **日期** | 2026-06-19 |
| **VPS** | `70.34.221.132:/opt/polymarket-bot` — 已 SFTP 同步上述 3 个 C++ 源文件并编译；**暂停中**（`logs/STOP_TRADING`） |
| **下一版代号** | `v0.10.0-endgame`（末段软顶分批 + hold 赢单 — **未开发**） |

---

## v0.9.0 已实现能力

### 核心策略（LIH）

- **Leg1**：某侧 ask ≤ `LIH_LEG1_MAX_PRICE`（默认 0.45），买更便宜的一边。
- **利润对冲**：`heavy_avg + light_ask ≤ LIH_TARGET_COMBINED`（默认 0.95）时买轻腿配平。
- **Flex 模式**（`LIH_REBALANCE_MODE=flex`）：heavy 侧 ask 低于均价 × `LIH_FLEX_DILUTE_RATIO` 时可稀释；leg1-only 阶段不 over-target 提前对冲。
- **Force 配平**（方案 1）：剩余 ≤ `LIH_FORCE_BALANCE_SECS`（默认 **60s**）时，即使 marginal > target 也尝试买轻腿关 gap（止损兜底，非盈利策略）。
- **Leg1 趋势过滤**（方案 3）：`LIH_LEG1_TREND_ALIGN=true` 时，YES 需 Binance spot 60s 内不跌，NO 需不涨；缺行情数据则放行。

### 风控与运维

- **启动即暂停**：每次 restart 写 `logs/STOP_TRADING`，RiskManager 默认 PAUSED；Web Resume 才开交易。
- **链上对齐**：成交后 `--positions-only` + 每 `LIH_CHAIN_RECONCILE_SEC`（10s）全量 reconcile；merge 取 per-leg max，不抹掉对侧腿。
- **Pending 成交**：CLOB 挂单异步轮询 `poll_lih_pending_fills`；45s 确认 dead 后 abandon 释放锁，策略下一轮可再试。
- **Live LIH 状态**：`logs/live_lih_state.json` 持久化；shadow 模式 `LIVE_LIH_DRY_RUN=true` 不发单。

### 主要 env（v0.9.0 推荐实盘基线）

```env
LIH_ENABLED=true
LIH_LEG1_MAX_PRICE=0.45
LIH_TARGET_COMBINED=0.95
LIH_LEG1_SHARES=10
LIH_FORCE_BALANCE_SECS=60
LIH_LEG1_TREND_ALIGN=true          # VPS 已开
LIH_TREND_LOOKBACK_SEC=60
LIH_REBALANCE_MODE=flex
LIH_LEG1_MIN_SECONDS_REMAINING=30
LIH_MIN_SECONDS_REMAINING=15
LIH_REBALANCE_COOLDOWN_SECONDS=5
LIH_ONE_SLOT_GLOBAL=true
LIH_MAX_USDC_PER_SLOT=10
```

---

## v0.9.0 已知限制

| 限制 | 说明 |
|------|------|
| 无末段分批 | Force 窗口内逻辑与整段相同，无 1.15 软顶、无分步追平 |
| 无「高价 hold」 | 末段不会因持有腿 ≥0.90 而跳过配平 |
| Pending 无即时重下单 | 挂单跟踪中 **不 retry**；abandon 后靠 detector 下一 tick（受 rebalance cooldown 约束） |
| VPS git 脏树 | 服务器本地改动导致 `git pull` 失败；部署需 SFTP 指定文件或清理后 pull |
| 最小份数 | 检测器按 **≥$1 USDC** 过滤；Polymarket 实际 **≥5 shares** 由下单前 `leg_meets_minimum` / 簿深度 resize 约束 |

---

## 待开发：`v0.10.0-endgame`（产品共识草案）

> 以下为用户 2026-06-19 讨论结论，**尚未编码**。

### 策略优先级（一句话）

**默认尽量配平** → 配平不了且 **顺势 + 价高** 才 hold 吃赢 → **逆势** 则分批买双边调仓减损（合价 >1 也可接受，总比单边全亏强）。

### 时间轴

```
secs_left > 60     → v0.9.0 现有逻辑（≤0.95 利润对冲）
secs_left ≤ 60     → 末段模式（主目标仍是配平）
secs_left ≤ 20     → 软顶可突破，全力关 gap（初版 20s，实战稳定后可改 15s）
```

### 末段决策（按顺序）

```
1. 能配平且合价可接受？ → 分批买缺的那腿（贵腿），缩小 gap
2. 仍配不平，但持有腿 ask≥0.90 且 Binance 顺势？ → Hold，等结算吃赢
3. 逆势，或价跌回 <0.89？ → 继续调仓：缺腿就补贵腿；必要时两边小步加买，
   用新均价算总合价，能少亏就少亏（>1.0 / 软顶 1.15 以内优先，最后 20s 可突破）
```

| 条件 | 动作 |
|------|------|
| 末段 + 能买到对面 | **优先配平**（5/10 份小步买缺腿） |
| 配平不划算 + ask **≥ 0.90** + **顺势** | **Hold**，不吃对冲亏损 |
| **逆势**，或 ask **< 0.89**，或从高位跌回 | **继续调仓减损**，不裸赌单边 |
| 合价 **> 1.0** | 可接受，只要比「单边全亏」好 |

> Hold 是配平路径走不通时的**例外**；不是默认选项。  
> 「买便宜腿」仅指调仓过程中**两边都可能动**，目的是压总亏损、关 gap，不是单独加仓赌方向。

### 分批规则

- **1.15 为软顶**（非目标）；能 ≤0.95 最好。
- **每步 5 或 10 shares**：gap 小 → 5 份；gap 大 → 10 份。
- 每步下单前重算：成交后 gap、port_avg、合价是否可接受。

### 成交与重试（对接现有代码）

- 已有：`poll_lih_pending_fills` 每秒查单；45s 确认 unmatched/cancelled 后 **abandon + 释放 inflight**。
- 已有：立即拒单 / 0 fill → `end_lih_rebalance_inflight`，5s rebalance cooldown 后可再评估。
- **末段待加强**：最后 20s 可缩短 cooldown 或 bypass，价格变后更快重试；pending abandon 后立即允许下一笔 endgame 单。

### 计划 env（草案）

```env
LIH_ENDGAME_SECS=60
LIH_ENDGAME_HOLD_ASK=0.90
LIH_ENDGAME_RESUME_HEDGE_ASK=0.89
LIH_ENDGAME_SOFT_CAP=1.15
LIH_ENDGAME_STEP_SHARES_SMALL=5
LIH_ENDGAME_STEP_SHARES_LARGE=10
LIH_ENDGAME_GAP_LARGE=10
LIH_ENDGAME_OVERRIDE_SECS=20
```

---

## 版本历史

| 版本 | Git | 摘要 |
|------|-----|------|
| **v0.9.0-lih-baseline** | `0268601` | Leg1 趋势过滤 + 60s force；启动暂停 + 链上 reconcile merge 修复 |
| v0.8.x | `6da72c1` | Reconcile merge 不抹对侧腿 |
| v0.8.x | `36c6e72` | 重启暂停、链上 reconcile、CLOB pending |
| v0.7.x | `82585f0` | 同窗口 hedge 匹配、history dedupe |

---

## 相关文档

- [`README.md`](../README.md) — 架构与日常命令
- [`docs/LIVE_LIH_ORDER_FLOW.md`](LIVE_LIH_ORDER_FLOW.md) — 下单链路
- [`docs/LIVE_SETUP.md`](LIVE_SETUP.md) — 实盘环境
- [`.env.example`](../.env.example) — 配置模板
