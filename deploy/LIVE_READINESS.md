# 实盘就绪清单 (Live Readiness)

当前 bot 为 **DH-only**（结构对冲）。默认 **纸面模式**，不会动用真钱。

在设置 `PAPER_MODE=false` 之前，请逐项确认。

---

## 1. 环境与密钥

| 步骤 | 命令 / 配置 | 通过标准 |
|------|-------------|----------|
| 纸面长跑 | `PAPER_MODE=true`，观察 24h+ | 无崩溃；DH 到期 PnL ≈ locked profit |
| 钱包鉴权 | `python test_auth.py` | API 凭证输出成功（**仅验钱包，不验 C++ 下单**） |
| 私钥 / Funder | `.env` 中 `POLYMARKET_PRIVATE_KEY`、`POLYMARKET_FUNDER` | 非占位符；live 启动时 bot 会校验 |
| L2 API Key | 容器启动时 `derive_and_update_keys.py` | `.env` 中有 `POLY_API_KEY/SECRET/PASSPHRASE` |
| Proxy 钱包 | 若用 Polymarket 代理：`POLYMARKET_SIGNER` ≠ `POLYMARKET_FUNDER` | 与 Polymarket 账户类型一致 |

---

## 2. 纸面 vs 实盘差异

| 能力 | 纸面 | 实盘 |
|------|------|------|
| DH 开仓 | 本地账本 | CLOB FAK 顺序双腿 |
| DH 到期 | 结构结算（locked PnL） | 账本结构结算 + **`AUTO_REDEEM` 链上 redeem** |
| 余额 | `PAPER_STARTING_BALANCE` / 持久化 JSON | `fetch_balance.py`（CLOB v2 + 链上 pUSD 回退）+ 60s 同步 |
| Binance 图 | 可选，不参与开仓 | 同左 |

---

## 3. 实盘 DH 流程（已实现）

1. 深度检查（asks 汇总）
2. **YES 腿 → NO 腿** 顺序下单（不重复记账）
3. NO 失败 → 自动 unwind YES
4. 到期 → 结构结算 + 异步 `redeem_positions.py`
5. Redeem 成功 → 同步链上余额

---

## 4. 首次实盘建议

1. **极小资金**（如 $20–50 USDC + 少量 MATIC 作 gas）
2. `PAPER_MODE=false`，`AUTO_REDEEM=true`
3. 手动盯日志关键词：
   - `[LIVE DH] OPENED`
   - `SETTLED ... RESOLVED`
   - `REDEEM OK` / `REDEEM FAIL`
4. 在 [Polygonscan](https://polygonscan.com/) 核对 redeem 交易
5. 确认 Polymarket 账户 USDC 与 bot 仪表盘余额一致

---

## 5. 已知限制（上实盘前知晓）

| 项目 | 状态 |
|------|------|
| DH 提前止盈 | 未实现（仅持有至窗口结束） |
| C++ 签名 vs SDK 向量测试 | 未自动化，需首单人工核对 |
| Fill 轮询 `GET /order/{id}` | 已实现（POST 后最多 5 次、150ms 间隔确认成交量） |
| 历史 LA 策略 | 已移除 |
| `test_auth` 通过 | **不等于** 可 unattended 实盘 |

---

## 6. 回滚纸面

```env
PAPER_MODE=true
```

然后：

```bash
docker compose restart bot
```

纸面状态见 `PAPER_STATE_PERSIST` / `logs/paper_state.json`。

---

## 7. 故障排查

| 现象 | 可能原因 |
|------|----------|
| `REDEEM FAIL` | RPC 超时、无 MATIC、condition_id 缺失、市场未 finalize |
| `Live DH closed without condition_id` | Gamma 未返回 conditionId（检查市场刷新日志） |
| 余额与链上不符 | redeem 未成功或 CLOB 余额未同步 |
| DH 未开仓 | 合价/折价未达阈值、冷却中、同 asset 已有持仓、腿 < $1 |

---

**结论：** 纸面可验证策略；实盘需按本清单 **小资金试跑 + 人工盯盘**，通过后再考虑加大规模。
