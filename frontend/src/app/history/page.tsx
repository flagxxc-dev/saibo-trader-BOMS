"use client";

import { useMemo, useState } from "react";
import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { TradeHistoryTable } from "@/components/history/TradeHistoryTable";
import { useLiveState, type TradeRecord } from "@/hooks/useLiveState";
import { History } from "lucide-react";

type FilterKey = "all" | "open" | "closed" | "LA" | "DH";

const filters: { key: FilterKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "open", label: "持仓中" },
  { key: "closed", label: "已平仓" },
  { key: "LA", label: "LA" },
  { key: "DH", label: "DH" },
];

function applyFilter(records: TradeRecord[], filter: FilterKey): TradeRecord[] {
  switch (filter) {
    case "open":
      return records.filter((r) => r.status === "open");
    case "closed":
      return records.filter((r) => r.status === "closed");
    case "LA":
      return records.filter((r) => r.strategy === "LA");
    case "DH":
      return records.filter((r) => r.strategy === "DH");
    default:
      return records;
  }
}

export default function HistoryPage() {
  const liveState = useLiveState();
  const [filter, setFilter] = useState<FilterKey>("all");

  const filtered = useMemo(
    () => applyFilter(liveState.tradeHistory, filter),
    [liveState.tradeHistory, filter]
  );

  const closedCount = liveState.tradeHistory.filter((r) => r.status === "closed").length;
  const openCount = liveState.tradeHistory.filter((r) => r.status === "open").length;
  const totalPnl = liveState.tradeHistory
    .filter((r) => r.status === "closed")
    .reduce((s, r) => s + r.pnlUsdc, 0);

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="交易历史"
          description="结构化成交记录：入场/出场价、份额、手续费与盈亏。纸面与实盘共用同一套字段。"
          icon={History}
        />

        {liveState.isPaperMode && (
          <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-[13px] text-amber-200">
            当前为<strong>纸面模式</strong>，下列记录来自 RiskManager 内存账本；重启 bot 后会清零。实盘切换后字段格式不变。
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-4 mb-5">
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">已平仓</p>
              <p className="text-xl font-mono font-bold text-white">{closedCount} 笔</p>
            </CardContent>
          </GlassCard>
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">持仓中</p>
              <p className="text-xl font-mono font-bold text-sky-400">{openCount} 笔</p>
            </CardContent>
          </GlassCard>
          <GlassCard>
            <CardContent className="pt-4 pb-4">
              <p className="text-[10px] uppercase tracking-widest text-white/30 mb-1">已实现盈亏</p>
              <p className={`text-xl font-mono font-bold ${totalPnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}
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
                  LA 单边 / DH 双边对冲 · 含入场/出场手续费拆分
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
