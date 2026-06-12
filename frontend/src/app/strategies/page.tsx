"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { useLiveState } from "@/hooks/useLiveState";

const ASSET_KEYS_5M = {
  BTC: "DH_ENABLE_5M_BTC",
  ETH: "DH_ENABLE_5M_ETH",
  SOL: "DH_ENABLE_5M_SOL",
} as const;

const ASSET_KEYS_15M = {
  BTC: "DH_ENABLE_15M_BTC",
  ETH: "DH_ENABLE_15M_ETH",
} as const;

function AssetToggleRow({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-2 pl-3 border-l border-white/10">
      <span className="text-[13px] font-mono text-white/75">{label}</span>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  );
}

export default function StrategiesPage() {
  const live = useLiveState();
  const [sumTarget, setSumTarget] = useState("0.95");
  const [minDiscount, setMinDiscount] = useState("0.03");
  const [cooldown, setCooldown] = useState("30");
  const [minRemaining, setMinRemaining] = useState("60");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const dhEnabled = live.status !== 3; // PAUSED
  const controlsDisabled = loading || live.status === 2;

  useEffect(() => {
    setSumTarget(live.dhSumTarget.toFixed(3));
    setMinDiscount(live.dhMinDiscount.toFixed(3));
    setCooldown(String(Math.round(live.dhCooldownSeconds)));
    setMinRemaining(String(Math.round(live.dhMinSecondsRemaining)));
  }, [live.dhSumTarget, live.dhMinDiscount, live.dhCooldownSeconds, live.dhMinSecondsRemaining]);

  const patchConfig = async (patch: Record<string, string>, okMessage: string) => {
    setLoading(true);
    setMessage("");
    try {
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "操作失败");
      setMessage(okMessage);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setLoading(false);
    }
  };

  const toggleWindow = (window: "5m" | "15m", enabled: boolean) => {
    const key = window === "5m" ? "DH_ENABLE_5M" : "DH_ENABLE_15M";
    return patchConfig({ [key]: enabled ? "true" : "false" }, `${window} 窗口已${enabled ? "开启" : "关闭"}交易`);
  };

  const toggleAsset = (envKey: string, label: string, enabled: boolean) =>
    patchConfig({ [envKey]: enabled ? "true" : "false" }, `${label} 已${enabled ? "开启" : "关闭"}`);

  const toggleDh = async (enabled: boolean) => {
    setLoading(true);
    setMessage("");
    try {
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: enabled ? "resume" : "pause",
          reason: enabled ? "DH enabled via web" : "DH disabled via web",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "操作失败");
      setMessage(enabled ? "检测器已启用" : "检测器已暂停");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patch: {
            DH_SUM_TARGET: sumTarget,
            DH_MIN_DISCOUNT: minDiscount,
            DH_COOLDOWN_SECONDS: cooldown,
            DH_MIN_SECONDS_REMAINING: minRemaining,
          },
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "保存失败");
      setMessage("策略参数已保存并热更新到运行中的 bot");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="策略配置"
          description="DH 结构对冲参数 — 保存后写入 .env 并立即生效。"
          icon={SlidersHorizontal}
        />

        <div className="mb-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-2.5 text-[13px] text-emerald-200/90">
          模式：<span className="font-mono">{live.isPaperMode ? "纸面" : "实盘"}</span>
          {" · "}
          状态：<span className="font-mono">{live.statusReason || (dhEnabled ? "ACTIVE" : "PAUSED")}</span>
        </div>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">对冲套利检测器 (DH)</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              扫描 Polymarket 5m/15m Up-Down 市场，当 YES + NO 合价低于目标时同时买入双腿。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex items-center justify-between">
              <Label htmlFor="dump-hedge" className="flex flex-col space-y-1">
                <span className="font-semibold text-white/90 text-[14px]">启用检测器</span>
                <span className="font-normal text-white/40 text-[12px] tracking-wide">
                  关闭后暂停新开仓（已有持仓不受影响）。
                </span>
              </Label>
              <Switch
                id="dump-hedge"
                checked={dhEnabled}
                disabled={controlsDisabled}
                onCheckedChange={toggleDh}
              />
            </div>

            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-4">
              <h4 className="text-[11px] font-medium tracking-widest uppercase text-white/40">窗口与币种开关</h4>
              <p className="text-[12px] text-white/40 leading-relaxed">
                先关窗口则该周期全部不交易；窗口开启后，可单独关闭某个币种（例如只跑 BTC 5m）。
              </p>

              <div className="space-y-3">
                <div className="flex items-center justify-between py-1">
                  <Label htmlFor="dh-5m" className="flex flex-col space-y-1">
                    <span className="font-semibold text-white/90 text-[14px]">5 分钟窗口</span>
                    <span className="font-normal text-white/40 text-[12px]">总开关</span>
                  </Label>
                  <Switch
                    id="dh-5m"
                    checked={live.dhEnable5m}
                    disabled={controlsDisabled}
                    onCheckedChange={(checked) => toggleWindow("5m", checked)}
                  />
                </div>
                <div className={`space-y-1 ${!live.dhEnable5m ? "opacity-40 pointer-events-none" : ""}`}>
                  <AssetToggleRow
                    label="BTC"
                    checked={live.dhEnable5mBtc}
                    disabled={controlsDisabled || !live.dhEnable5m}
                    onChange={(checked) => toggleAsset(ASSET_KEYS_5M.BTC, "5m BTC", checked)}
                  />
                  <AssetToggleRow
                    label="ETH"
                    checked={live.dhEnable5mEth}
                    disabled={controlsDisabled || !live.dhEnable5m}
                    onChange={(checked) => toggleAsset(ASSET_KEYS_5M.ETH, "5m ETH", checked)}
                  />
                  <AssetToggleRow
                    label="SOL"
                    checked={live.dhEnable5mSol}
                    disabled={controlsDisabled || !live.dhEnable5m}
                    onChange={(checked) => toggleAsset(ASSET_KEYS_5M.SOL, "5m SOL", checked)}
                  />
                </div>
              </div>

              <div className="space-y-3 border-t border-white/5 pt-4">
                <div className="flex items-center justify-between py-1">
                  <Label htmlFor="dh-15m" className="flex flex-col space-y-1">
                    <span className="font-semibold text-white/90 text-[14px]">15 分钟窗口</span>
                    <span className="font-normal text-white/40 text-[12px]">总开关</span>
                  </Label>
                  <Switch
                    id="dh-15m"
                    checked={live.dhEnable15m}
                    disabled={controlsDisabled}
                    onCheckedChange={(checked) => toggleWindow("15m", checked)}
                  />
                </div>
                <div className={`space-y-1 ${!live.dhEnable15m ? "opacity-40 pointer-events-none" : ""}`}>
                  <AssetToggleRow
                    label="BTC"
                    checked={live.dhEnable15mBtc}
                    disabled={controlsDisabled || !live.dhEnable15m}
                    onChange={(checked) => toggleAsset(ASSET_KEYS_15M.BTC, "15m BTC", checked)}
                  />
                  <AssetToggleRow
                    label="ETH"
                    checked={live.dhEnable15mEth}
                    disabled={controlsDisabled || !live.dhEnable15m}
                    onChange={(checked) => toggleAsset(ASSET_KEYS_15M.ETH, "15m ETH", checked)}
                  />
                </div>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">合价目标 (DH_SUM_TARGET)</Label>
                <Input value={sumTarget} onChange={(e) => setSumTarget(e.target.value)} className="font-mono bg-white/5 border-white/10" />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">最小折价 (DH_MIN_DISCOUNT)</Label>
                <Input value={minDiscount} onChange={(e) => setMinDiscount(e.target.value)} className="font-mono bg-white/5 border-white/10" />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">冷却秒数 (DH_COOLDOWN_SECONDS)</Label>
                <Input value={cooldown} onChange={(e) => setCooldown(e.target.value)} className="font-mono bg-white/5 border-white/10" />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">窗口剩余秒数下限 (DH_MIN_SECONDS_REMAINING)</Label>
                <Input value={minRemaining} onChange={(e) => setMinRemaining(e.target.value)} className="font-mono bg-white/5 border-white/10" />
              </div>
            </div>

            <div className="bg-white/5 p-4 rounded-xl border border-white/10 text-[13px] text-white/50">
              当前扫描 {live.marketsScanned} 个市场 · 5m{" "}
              {[live.dhEnable5mBtc && "BTC", live.dhEnable5mEth && "ETH", live.dhEnable5mSol && "SOL"]
                .filter(Boolean)
                .join(" · ") || "全关"}{" "}
              · 15m{" "}
              {[live.dhEnable15mBtc && "BTC", live.dhEnable15mEth && "ETH"].filter(Boolean).join(" · ") || "全关"}
            </div>

            <div className="flex items-center justify-between pt-2">
              {message && <p className="text-[13px] text-amber-200/90">{message}</p>}
              <Button onClick={handleSave} disabled={loading} size="lg" variant="glass" className="ml-auto px-8 font-extrabold tracking-tight rounded-2xl">
                {loading ? "保存中..." : "保存策略参数"}
              </Button>
            </div>
          </CardContent>
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
