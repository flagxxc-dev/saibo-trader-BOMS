# 归档说明

本目录存放 **Dump Hedge (DH) 纯策略** 的配置快照与说明。主仓库默认已切换为 **LIH（分腿对冲）**。

## 为什么归档 DH

- DH 逻辑在纸面/回测上已成熟（双边折价同时开仓）
- 实盘中与 HFT / 做市商抢同一笔合价单，**很难成交**，策略无法发挥
- LIH 改为「先买便宜腿、再 rebalance」，更适合当前竞争环境

## 目录内容

| 文件 | 说明 |
|------|------|
| [`.env.dh-only.example`](./.env.dh-only.example) | DH 专用 `.env` 模板（`LIH_ENABLED=false`） |
| [`README.md`](./README.md) | 本说明 |

## 如何恢复 DH-only 运行

1. 复制配置：`cp archive/dh-only/.env.dh-only.example .env`（或手动改现有 `.env`）
2. 关键项：
   - `LIH_ENABLED=false`
   - `DH_ENABLE_5M_*` / `DH_SUM_TARGET` 等按模板填写
3. 重新构建并启动 bot（与平时相同）：
   ```powershell
   docker compose build bot
   docker compose up -d bot
   ```

## 代码位置（未删除）

DH 检测与执行代码仍在主仓库，仅默认不运行：

- `trading-core/src/signals/DumpHedgeDetector.{h,cpp}`
- `main.cpp` 中当 `LIH_ENABLED=true` 时不调用 DH 路径

如需长期 frozen 版本，可在本地打 tag，例如：

```bash
git tag -a dh-only-legacy -m "DH-only before LIH primary"
```

## 部署建议

- **新服务器 / 新上传**：使用主仓库默认配置（LIH）
- **仅复现 DH 纸面实验**：使用本目录 `.env.dh-only.example`
