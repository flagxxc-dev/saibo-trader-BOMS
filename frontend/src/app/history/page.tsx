"use client";

import { useMemo, useState, useEffect } from "react";
import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { TradeHistoryTable } from "@/components/history/TradeHistoryTable";
import { useLiveState, type TradeRecord } from "@/hooks/useLiveState";
import { resolveTradeWindow } from "@/lib/tradeWindow";
import { mergeTradeHistory, type ClobTradeFill } from "@/lib/clobTrades";
import {
  cumulativeClosedTrades,
  isLihPrimary,
  strategyShortLabel,
} from "@/lib/strategyMode";
import { History } from "lucide-react";

type FilterKey = "all" | "open" | "closed" | "5m" | "15m";

const filters: { key: FilterKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "open", label: "持仓中" },
  { key: "closed", label: "已平仓" },
  { key: "5m", label: "5m" },
  { key: "15m", label: "15m" },
];

function applyFilter(records: TradeRecord[], filter: FilterKey): TradeRecord[] {
  switch (filter) {
    case "open":
      return records.filter((r) => r.status === "open");
    case "closed":
      return records.filter((r) => r.status === "closed");
    case "5m":
      return records.filter((r) => resolveTradeWindow(r.market, r.windowMinutes) === 5);
    case "15m":
      return records.filter((r) => resolveTradeWindow(r.market, r.windowMinutes) === 15);
    default:
      return records;
  }
}

function closedPnlForWindow(records: TradeRecord[], minutes: number): number {
  return records
    .filter(
      (r) =>
        r.status === "closed" &&
        r.exitReason !== "CLOB_FILL" &&
        resolveTradeWindow(r.market, r.windowMinutes) === minutes
    )
    .reduce((s, r) => s + r.pnlUsdc, 0);
}

export default function HistoryPage() {
  const liveState = useLiveState();
  const [filter, setFilter] = useState<FilterKey>("all");
  const [clobFills, setClobFills] = useState<ClobTradeFill[]>([]);
  const lihMode = isLihPrimary(liveState);
  const strategyLabel = strategyShortLabel(liveState);
  const closedRoundCount = cumulativeClosedTrades(liveState);

  useEffect(() => {
    if (liveState.isPaperMode) {
      setClobFills([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/clob/trades?limit=200");
        const data = await res.json();
        if (!res.ok || cancelled) return;
        const rows = Array.isArray(data.trades) ? data.trades : [];
        setClobFills(rows as ClobTradeFill[]);
      } catch {
        if (!cancelled) setClobFills([]);
      }
    })();
    const timer = setInterval(() => {
      void fetch("/api/clob/trades?limit=200")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled && Array.isArray(data.trades)) setClobFills(data.trades as ClobTradeFill[]);
        })
        .catch(() => {});
    }, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [liveState.isPaperMode]);

  const tradeHistory = useMemo(
    () => mergeTradeHistory(liveState.tradeHistory, clobFills, liveState.tradesBaselineTs ?? 0),
    [liveState.tradeHistory, clobFills, liveState.tradesBaselineTs]
  );

  const filtered = useMemo(
    () => applyFilter(tradeHistory, filter),
    [tradeHistory, filter]
  );

  const closedCount = tradeHistory.filter((r) => r.status === "closed").length;
  const openCount = tradeHistory.filter((r) => r.status === "open").length;
  const totalPnl = tradeHistory
    .filter((r) => r.status === "closed" && r.exitReason !== "CLOB_FILL")
    .reduce((s, r) => s + r.pnlUsdc, 0);
  const pnl5m = closedPnlForWindow(tradeHistory, 5);
  const pnl15m = closedPnlForWindow(tradeHistory, 15);

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="交易历史"
          description={
            lihMode
              ? "LIH 分腿成交记录：leg1 入场、rebalance、结算与盈亏。按 5m / 15m 窗口区分。"
              : "DH 结构化成交记录：入场/出场价、份额、手续费与盈亏。按 5m / 15m 窗口区分。"
          }
          icon={History}
        />

        {liveState.isPaperMode && (
          <div className="mb-4 rounded-lg border border-amber-500/25 bg-amber-500/8 px-4 py-2.5 text-[13px] text-amber-100/90">
            当前为<strong>纸面模式</strong>。记录写入本地状态文件，重启 bot 后保留。
          </div>
        )}
        {!liveState.isPaperMode && liveState.tradesBaselineTs && liveState.tradesBaselineTs > 0 && (
          <div className="mb-4 rounded-lg border border-emerald-500/25 bg-emerald-500/8 px-4 py-2.5 text-[13px] text-emerald-100/90">
            实盘统计已从{" "}
            <strong>
              {new Date(liveState.tradesBaselineTs * 1000).toLocaleString("zh-CN")}
            </strong>{" "}
            起重新计数，此前误触发的成交不会计入。
          </div>
        )}
        {!liveState.isPaperMode && clobFills.length > 0 && liveState.tradeHistory.length === 0 && !liveState.tradesBaselineTs && (
          <div className="mb-4 rounded-lg border border-sky-500/25 bg-sky-500/8 px-4 py-2.5 text-[13px] text-sky-100/90">
            已从 Polymarket 链上成交同步 <strong>{clobFills.length}</strong> 笔记录（bot 内存状态为空时自动补全）。
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 mb-5">
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">持仓中</p>
              <p className="text-xl font-mono font-bold text-sky-400">{openCount} 笔</p>
            </CardContent>
          </GlassCard>
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">累计 {strategyLabel} 成交</p>
              <p className="text-xl font-mono font-bold text-white">{closedRoundCount} 笔</p>
              <p className="text-[10px] font-mono text-white/35 mt-1">已平仓 {closedCount}</p>
            </CardContent>
          </GlassCard>
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">已实现盈亏</p>
              <p className={`text-xl font-mono font-bold ${totalPnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
              </p>
              <p className="text-[10px] font-mono text-white/35 mt-1">
                5m {pnl5m >= 0 ? "+" : ""}${pnl5m.toFixed(2)} · 15m {pnl15m >= 0 ? "+" : ""}${pnl15m.toFixed(2)}
              </p>
            </CardContent>
          </GlassCard>
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">账户余额</p>
              <p className="text-xl font-mono font-bold text-white">${liveState.balance.toFixed(2)}</p>
            </CardContent>
          </GlassCard>
        </div>

        <GlassCard>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">
                  成交明细
                </CardTitle>
                <CardDescription className="text-white/40 text-[13px] mt-1">
                  {lihMode
                    ? "LIH 分腿对冲 · 含 leg1 / rebalance 与结算手续费"
                    : "5m / 15m 双边折价对冲 · 含入场/出场手续费拆分"}
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {filters.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => setFilter(f.key)}
                    className={`px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all ${
                      filter === f.key
                        ? "bg-white/15 text-white ring-1 ring-white/20"
                        : "bg-white/5 text-white/40 hover:text-white/70"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <TradeHistoryTable
              records={filtered}
              emptyText={filter === "all" ? "暂无交易记录，检测到信号并成交后会出现在此。" : "该筛选条件下暂无记录。"}
            />
          </CardContent>
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
