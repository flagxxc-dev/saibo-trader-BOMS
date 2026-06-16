# 归档目录

| 路径 | 说明 |
|------|------|
| [`dh-only/`](./dh-only/) | Dump Hedge (DH) 纯策略配置快照与恢复说明 |

**主仓库默认策略：LIH（分腿对冲）**。DH 代码仍保留在 `trading-core/src/signals/`，设置 `LIH_ENABLED=false` 可恢复。

新部署 / 上传服务器请使用根目录 `.env.example`（LIH 默认），勿使用 `archive/dh-only/` 除非刻意复现 DH 实验。
