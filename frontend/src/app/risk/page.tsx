"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { DemoBanner } from "@/components/shared/DemoBanner";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/shared/GlassCard";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import { ShieldAlert } from "lucide-react";

export default function RiskPage() {
  const [maxPosition, setMaxPosition] = useState([8]);
  const [dailyLoss, setDailyLoss] = useState([20]);
  const [drawdownKill, setDrawdownKill] = useState([40]);
  const [loading, setLoading] = useState(false);

  const handleSave = async () => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      alert("演示：请修改 .env 中的 RISK_* 参数并重启 bot。");
    }, 1000);
  };

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="风控限额"
          description="控制自动安全阈值（演示）。"
          icon={ShieldAlert}
        />

        <DemoBanner hint="真实限额由 .env 中 RISK_* 变量控制，修改后重启 bot。" />

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">单笔最大仓位比例</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              单笔交易可占用的最大账户余额百分比。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-white/90 font-medium text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-semibold text-white">{maxPosition[0]}%</span>
            </div>
            <Slider value={maxPosition} onValueChange={(val) => setMaxPosition(val as number[])} max={50} step={1} className="py-4" />
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">日亏损上限</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              当日累计亏损达到该比例时，暂停交易 24 小时。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-white/90 font-medium text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-semibold text-amber-400">-{dailyLoss[0]}%</span>
            </div>
            <Slider value={dailyLoss} onValueChange={(val) => setDailyLoss(val as number[])} max={100} step={1} className="py-4" />
          </CardContent>
        </GlassCard>

        <GlassCard className="border-red-500/20">
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-red-400">总回撤熔断</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              账户总值相对初始余额跌破该比例时，平仓并永久停止交易。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-red-400/80 font-bold text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-bold text-red-400">-{drawdownKill[0]}%</span>
            </div>
            <Slider
              value={drawdownKill}
              onValueChange={(val) => setDrawdownKill(val as number[])}
              max={100}
              step={1}
              className="[&_[role=slider]]:bg-red-400 [&_[role=slider]]:border-red-400 py-4"
            />
          </CardContent>
          <CardFooter className="bg-red-500/5 border-t border-red-500/10 py-3 mt-4">
            <p className="text-[11px] text-red-400/60 font-medium flex items-center gap-2 tracking-wide">
              <ShieldAlert className="h-3.5 w-3.5" />
              修改此值需要额外安全验证。
            </p>
          </CardFooter>
        </GlassCard>

        <div className="flex justify-end pt-4">
          <Button onClick={handleSave} disabled={loading} size="lg" variant="glass" className="px-8 font-extrabold tracking-tight rounded-2xl">
            {loading ? "保存中..." : "保存风控限额"}
          </Button>
        </div>
      </PageContainer>
    </DashboardLayout>
  );
}
