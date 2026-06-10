"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { DemoBanner } from "@/components/shared/DemoBanner";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";

export default function StrategiesPage() {
  const [latencyArbEnabled, setLatencyArbEnabled] = useState(true);
  const [dumpHedgeEnabled, setDumpHedgeEnabled] = useState(true);

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="策略配置"
          description="开关自动化策略检测器（演示）。"
          icon={SlidersHorizontal}
        />

        <DemoBanner hint="策略开关与参数读取自 .env，重启 bot 后生效。此页控件不会写入核心。" />

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">延迟套利检测器</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              利用 Binance 现货价与 Polymarket 订单簿之间的定价延迟进行套利。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <Label htmlFor="latency-arb" className="flex flex-col space-y-1">
                <span className="font-semibold text-white/90 text-[14px]">启用检测器</span>
                <span className="font-normal text-white/40 text-[12px] tracking-wide">
                  在执行循环中每 50ms 评估一次。
                </span>
              </Label>
              <Switch id="latency-arb" checked={latencyArbEnabled} onCheckedChange={setLatencyArbEnabled} />
            </div>

            <div className="bg-white/5 p-4 rounded-xl border border-white/10">
              <h4 className="text-[11px] font-medium tracking-widest uppercase text-white/40 mb-3">当前参数</h4>
              <ul className="text-[13px] space-y-2.5">
                <li className="flex justify-between">
                  <span className="text-white/50">标的</span>
                  <span className="font-mono text-white/90">BTC / ETH / SOL</span>
                </li>
                <li className="flex justify-between">
                  <span className="text-white/50">最小边际</span>
                  <span className="font-mono text-white/90">0.020（含手续费）</span>
                </li>
              </ul>
            </div>
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">对冲套利检测器</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              扫描订单簿，寻找 Yes + No 合计价低于 1.0 的结构套利机会。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <Label htmlFor="dump-hedge" className="flex flex-col space-y-1">
                <span className="font-semibold text-white/90 text-[14px]">启用检测器</span>
                <span className="font-normal text-white/40 text-[12px] tracking-wide">
                  在订单簿中寻找结构性折价机会。
                </span>
              </Label>
              <Switch id="dump-hedge" checked={dumpHedgeEnabled} onCheckedChange={setDumpHedgeEnabled} />
            </div>

            <div className="bg-white/5 p-4 rounded-xl border border-white/10">
              <h4 className="text-[11px] font-medium tracking-widest uppercase text-white/40 mb-3">当前参数</h4>
              <ul className="text-[13px] space-y-2.5">
                <li className="flex justify-between">
                  <span className="text-white/50">合计目标</span>
                  <span className="font-mono text-white/90">&lt; 0.93</span>
                </li>
                <li className="flex justify-between">
                  <span className="text-white/50">最小折价</span>
                  <span className="font-mono text-white/90">2%（扣费后）</span>
                </li>
              </ul>
            </div>
          </CardContent>
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
