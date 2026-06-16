"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/shared/GlassCard";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCallback, useEffect, useState } from "react";
import { strategyShortLabel } from "@/lib/strategyMode";
import { useLiveState } from "@/hooks/useLiveState";
import { ShieldAlert } from "lucide-react";

function toSliderArray(val: unknown): number[] {
  if (val === null || val === undefined) return [];
  if (Array.isArray(val)) {
    return val.map((n) => Number(n)).filter((n) => Number.isFinite(n));
  }
  const n = Number(val);
  return Number.isFinite(n) ? [n] : [];
}

/** Slider state may be number[] or a bare number if base-ui omits the array wrapper. */
function sliderScalar(val: number | number[], fallback: number): number {
  const v = Array.isArray(val) ? val[0] : val;
  return Number.isFinite(v) ? v : fallback;
}

export default function RiskPage() {
  const live = useLiveState();
  const strategyLabel = strategyShortLabel(live);
  const [maxPosition, setMaxPosition] = useState([8]);
  const [dailyLoss, setDailyLoss] = useState([20]);
  const [drawdownKill, setDrawdownKill] = useState([40]);
  const [maxConcurrent, setMaxConcurrent] = useState([3]);
  const [lihSlotCapUsdc, setLihSlotCapUsdc] = useState("0");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const loadSlotCap = useCallback(async () => {
    try {
      const res = await fetch("/api/bot/config");
      if (!res.ok) return;
      const data = (await res.json()) as { config?: Record<string, string> };
      const v = data.config?.LIH_MAX_USDC_PER_SLOT;
      if (v !== undefined && v !== "") setLihSlotCapUsdc(v);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadSlotCap();
  }, [loadSlotCap]);

  useEffect(() => {
    setMaxPosition([Math.round(sliderScalar(live.riskMaxPositionFraction, 0.08) * 100)]);
    setDailyLoss([Math.round(sliderScalar(live.riskDailyLossLimit, 0.2) * 100)]);
    setDrawdownKill([Math.round(sliderScalar(live.riskTotalDrawdownKill, 0.4) * 100)]);
    setMaxConcurrent([Math.round(sliderScalar(live.riskMaxConcurrentPositions, 1))]);
  }, [
    live.riskMaxPositionFraction,
    live.riskDailyLossLimit,
    live.riskTotalDrawdownKill,
    live.riskMaxConcurrentPositions,
  ]);

  const handleSave = async () => {
    setLoading(true);
    setMessage("");
    try {
      const posPct = sliderScalar(maxPosition, 8);
      const lossPct = sliderScalar(dailyLoss, 20);
      const killPct = sliderScalar(drawdownKill, 40);
      const concurrent = sliderScalar(maxConcurrent, 1);
      const slotCap = lihSlotCapUsdc.trim() || "0";
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patch: {
            RISK_MAX_POSITION_FRACTION: (posPct / 100).toFixed(4),
            RISK_DAILY_LOSS_LIMIT: (lossPct / 100).toFixed(4),
            RISK_TOTAL_DRAWDOWN_KILL: (killPct / 100).toFixed(4),
            RISK_MAX_CONCURRENT_POSITIONS: String(Math.round(concurrent)),
            LIH_MAX_USDC_PER_SLOT: slotCap,
          },
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "保存失败");
      setMessage("风控限额已保存并热更新");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败");
    } finally {
      setLoading(false);
    }
  };

  const resetKill = async () => {
    if (!confirm("确认重置熔断开关？仅在误触发后使用。")) return;
    setLoading(true);
    try {
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "reset_kill" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "重置失败");
      setMessage("熔断已重置");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "重置失败");
    } finally {
      setLoading(false);
    }
  };

  const autoSlotCap = live.balance * live.riskMaxPositionFraction;
  const slotCapNum = parseFloat(lihSlotCapUsdc);
  const effectiveSlotCap =
    Number.isFinite(slotCapNum) && slotCapNum > 0 ? slotCapNum : autoSlotCap;

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="风控限额"
          description="实时风控阈值 — 保存后写入 .env 并立即生效。"
          icon={ShieldAlert}
        />

        <div className="mb-4 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-[13px] text-white/60">
          当前余额 <span className="font-mono text-white/90">${live.balance.toFixed(2)}</span>
          {" · "}
          日盈亏 <span className={`font-mono ${live.dailyPnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {live.dailyPnl >= 0 ? "+" : ""}{live.dailyPnl.toFixed(2)}
          </span>
          {" · "}
          状态 <span className="font-mono text-white/90">{live.statusReason || `code ${live.status}`}</span>
        </div>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">单笔最大仓位比例</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              每一笔 LEG1 / 配平订单可占用的最大余额百分比（当前约 ${(live.balance * live.riskMaxPositionFraction).toFixed(2)}）。
              这是单笔上限，不是一整局 5 分钟盘的总预算。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-white/90 font-medium text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-semibold text-white">{sliderScalar(maxPosition, 8)}%</span>
            </div>
            <Slider
              value={maxPosition}
              onValueChange={(val) => setMaxPosition(toSliderArray(val))}
              min={1}
              max={90}
              step={1}
              className="py-4"
            />
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">
              LIH 每局累计上限 (LIH_MAX_USDC_PER_SLOT)
            </CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              同一资产 + 同一窗口（如 BTC 5 分钟盘）整局 LEG1 与配平累计最多可投入的 USDC。
              填 <span className="font-mono text-white/55">0</span> 表示跟随上方单笔比例（当前约 ${autoSlotCap.toFixed(2)}）。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-4">
              <div className="flex-1 space-y-2">
                <Label className="text-white/90 font-medium text-[14px]">美元上限</Label>
                <Input
                  value={lihSlotCapUsdc}
                  onChange={(e) => setLihSlotCapUsdc(e.target.value)}
                  placeholder="0 = 余额×比例"
                  className="font-mono bg-white/5 border-white/10 max-w-[200px]"
                />
              </div>
              <p className="text-[13px] text-white/45 pb-2">
                当前生效约{" "}
                <span className="font-mono text-white/80">${effectiveSlotCap.toFixed(2)}</span>
                {" / 局"}
              </p>
            </div>
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">最大并发持仓</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              同时持有的 {strategyLabel} 仓位数量上限（当前开仓 {live.openPositions}）。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-white/90 font-medium text-[14px]">数量</Label>
              <span className="font-mono text-2xl font-semibold text-white">{sliderScalar(maxConcurrent, 3)}</span>
            </div>
            <Slider value={maxConcurrent} onValueChange={(val) => setMaxConcurrent(toSliderArray(val))} min={1} max={20} step={1} className="py-4" />
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">日亏损上限</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              当日累计亏损达到该比例时，暂停交易至 UTC 午夜。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-white/90 font-medium text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-semibold text-amber-400">-{sliderScalar(dailyLoss, 20)}%</span>
            </div>
            <Slider value={dailyLoss} onValueChange={(val) => setDailyLoss(toSliderArray(val))} max={100} step={1} className="py-4" />
          </CardContent>
        </GlassCard>

        <GlassCard className="border-red-500/20">
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-red-400">总回撤熔断</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              账户总值相对峰值跌破该比例时触发永久停止（需手动重置）。可调范围 5%–100%。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between items-center mb-2">
              <Label className="text-red-400/80 font-bold text-[14px]">数值</Label>
              <span className="font-mono text-2xl font-bold text-red-400">-{sliderScalar(drawdownKill, 40)}%</span>
            </div>
            <Slider
              value={drawdownKill}
              onValueChange={(val) => setDrawdownKill(toSliderArray(val))}
              min={5}
              max={100}
              step={1}
              className="[&_[role=slider]]:bg-red-400 [&_[role=slider]]:border-red-400 py-4"
            />
          </CardContent>
          {live.status === 2 && (
            <CardFooter className="bg-red-500/5 border-t border-red-500/10 py-3 mt-4 flex justify-between">
              <p className="text-[11px] text-red-400/80 font-medium flex items-center gap-2 tracking-wide">
                <ShieldAlert className="h-3.5 w-3.5" />
                熔断已触发 — 需手动重置后才能恢复交易
              </p>
              <Button size="sm" variant="destructive" onClick={resetKill} disabled={loading}>
                重置熔断
              </Button>
            </CardFooter>
          )}
        </GlassCard>

        <div className="flex items-center justify-between pt-4">
          {message && <p className="text-[13px] text-amber-200/90">{message}</p>}
          <Button onClick={handleSave} disabled={loading} size="lg" variant="glass" className="ml-auto px-8 font-extrabold tracking-tight rounded-2xl">
            {loading ? "保存中..." : "保存风控限额"}
          </Button>
        </div>
      </PageContainer>
    </DashboardLayout>
  );
}
