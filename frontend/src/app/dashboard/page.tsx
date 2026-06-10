"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle } from "@/components/shared/GlassCard";
import { TradingPanels } from "@/components/dashboard/TradingPanels";
import { PriceChart } from "@/components/dashboard/PriceChart";
import { useLiveState } from "@/hooks/useLiveState";
import { coreStatusLabel } from "@/lib/coreStatus";
import { Activity, DollarSign, Briefcase, Percent, TrendingUp } from "lucide-react";
import { useState, useEffect } from "react";

export default function DashboardPage() {
  const liveState = useLiveState();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const pnlColor = liveState.totalPnl >= 0 ? "text-emerald-400" : "text-red-400";
  const coreStatus = coreStatusLabel(liveState.status);

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="仪表盘"
          description="实时行情、持仓与成交流水。"
          icon={Activity}
        />

        {liveState.isPaperMode && (
          <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-[13px] text-amber-200">
            📋 <strong>纸面交易模式</strong> — 以下为纸面数据，不会动用真实资金。机制与实盘一致：策略信号已扣除约 {(liveState.feeRate * 100).toFixed(1)}% 手续费边际，开仓/平仓时从余额扣减 taker 费。
          </div>
        )}

        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-4 mb-5">
          <GlassCard>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-[11px] font-medium tracking-widest uppercase text-white/40">总余额</CardTitle>
              <DollarSign className="h-4 w-4 text-white/20" />
            </CardHeader>
            <CardContent className="pb-4">
              <div className="text-2xl font-mono font-extrabold tracking-tighter text-white">
                ${liveState.balance.toFixed(2)}
              </div>
              <p className={`text-xs font-mono mt-3 ${pnlColor}`}>
                累计盈亏 {liveState.totalPnl >= 0 ? "+" : ""}${liveState.totalPnl.toFixed(2)}
                <span className="text-white/30 ml-2">今日 {liveState.dailyPnl >= 0 ? "+" : ""}${liveState.dailyPnl.toFixed(2)}</span>
              </p>
            </CardContent>
          </GlassCard>

          <GlassCard>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-[11px] font-medium tracking-widest uppercase text-white/40">持仓数量</CardTitle>
              <Briefcase className="h-4 w-4 text-white/20" />
            </CardHeader>
            <CardContent className="pb-4">
              <div className="text-2xl font-mono font-extrabold tracking-tighter text-white">
                {liveState.openPositions}
              </div>
              <p className="text-xs text-white/40 mt-3">
                累计成交 {liveState.totalTrades + liveState.totalDhTrades} 笔
              </p>
            </CardContent>
          </GlassCard>

          <GlassCard>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-[11px] font-medium tracking-widest uppercase text-white/40">胜率</CardTitle>
              <Percent className="h-4 w-4 text-white/20" />
            </CardHeader>
            <CardContent className="pb-4">
              <div className="text-2xl font-mono font-extrabold tracking-tighter text-white">
                {(liveState.winRate * 100).toFixed(1)}%
              </div>
              <p className="text-xs text-white/40 mt-3">历史平仓胜率</p>
            </CardContent>
          </GlassCard>

          <GlassCard>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-[11px] font-medium tracking-widest uppercase text-white/40">核心状态</CardTitle>
              <TrendingUp className="h-4 w-4 text-emerald-400/60" />
            </CardHeader>
            <CardContent className="pb-4">
              <div className="flex items-center gap-3">
                <span className="relative flex h-2 w-2">
                  {coreStatus.pulse && (
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  )}
                  <span
                    className={`relative inline-flex rounded-full h-2 w-2 ${
                      liveState.status === 0 ? "bg-emerald-400" : liveState.status === 2 ? "bg-red-400" : "bg-amber-400"
                    }`}
                  />
                </span>
                <span className={`text-2xl font-mono font-extrabold tracking-tighter ${coreStatus.color}`}>
                  {coreStatus.text}
                </span>
              </div>
              {liveState.statusReason && (
                <p className="text-[10px] text-white/35 mt-2 font-mono leading-relaxed line-clamp-2" title={liveState.statusReason}>
                  {liveState.statusReason}
                </p>
              )}
              <p className="text-xs text-white/30 mt-3 font-mono">
                更新：{mounted ? new Date(liveState.timestamp || Date.now()).toLocaleTimeString("zh-CN") : "--:--:--"}
              </p>
            </CardContent>
          </GlassCard>
        </div>

        <div className="mb-5">
          <PriceChart
            btcPrice={liveState.btcPrice}
            ethPrice={liveState.ethPrice}
            solPrice={liveState.solPrice}
            timestamp={liveState.timestamp}
          />
        </div>

        <TradingPanels liveState={liveState} />
      </PageContainer>
    </DashboardLayout>
  );
}
