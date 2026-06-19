# LIH 策略版本留档

本文档记录 **Leg-In Hedge（LIH）** 实盘栈的功能版本、配置基线与待开发项。  
Git 以 commit hash 为准；语义版本便于口头对齐。

---

## 当前基线：`v0.10.0-endgame`

| 项 | 值 |
|---|---|
| **Git** | `9b9c706` — `feat: LIH endgame batch hedge with soft cap and on-trend hold` |
| **VPS** | `70.34.221.132` — 已部署；**暂停中**（`STOP_TRADING`） |

---

## v0.10.0 新增（末段）

- **末段窗口** `LIH_ENDGAME_SECS=60`：剩余 ≤60s 且有 gap 时进入 endgame（替代旧 force 逻辑）。
- **配平优先**：5/10 份小步买缺腿；gap≥10 用 10 份，否则 5 份。
- **软顶** `LIH_ENDGAME_SOFT_CAP=1.15`；最后 `LIH_ENDGAME_OVERRIDE_SECS=20` 可突破软顶关 gap。
- **Hold 例外**：持有腿 ask≥0.90 且 Binance 顺势 → 不配平，等结算。
- **恢复配平**：ask 跌回 <0.89 或逆势 → 继续 endgame 配平。
- **末段重试**：最后 20s rebalance cooldown 降至 1s（`LIH_ENDGAME_OVERRIDE_COOLDOWN`）。

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

## v0.10.0 已知限制

| 限制 | 说明 |
|------|------|
| Pending 无即时重下单 | 挂单跟踪中不 retry；abandon 后下一 tick 再试（末段最后 20s cooldown=1s） |
| 末段仅买缺腿 | 主路径 CompleteHedge；逆势「双边调仓」仍通过多步补缺腿实现，无独立 paired 末段模式 |
| VPS git 脏树 | 服务器 `git pull` 可能失败；部署需 SFTP 或清理本地改动 |

---

## 待开发

_(none — endgame shipped in v0.10)_

<!--
Legacy draft below retained for history.
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

-->

---

## 版本历史

| 版本 | Git | 摘要 |
|------|-----|------|
| **v0.10.0-endgame** | _(pending)_ | 末段分批配平、软顶 1.15、顺势 hold ≥0.90、20s override |
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
