"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import {
  GlassCard,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/shared/GlassCard";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { SlidersHorizontal, ChevronDown, ChevronUp, Archive } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useLiveState } from "@/hooks/useLiveState";
import { isLihPrimary } from "@/lib/strategyMode";

type TradingMode = "stopped" | "shadow" | "live";

function deriveTradingMode(live: ReturnType<typeof useLiveState>): TradingMode {
  if (!live.botStreamConnected || live.status !== 0) return "stopped";
  if (live.liveLihDryRun === false) return "live";
  return "shadow";
}

const TRADING_MODE_LABEL: Record<TradingMode, string> = {
  stopped: "停止",
  shadow: "Shadow 运行",
  live: "实盘运行",
};

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

function MarketTogglesSection({
  live,
  controlsDisabled,
  onPatch,
}: {
  live: ReturnType<typeof useLiveState>;
  controlsDisabled: boolean;
  onPatch: (patch: Record<string, string>, okMessage: string) => Promise<void>;
}) {
  const toggleWindow = (window: "5m" | "15m", enabled: boolean) =>
    onPatch(
      { [window === "5m" ? "DH_ENABLE_5M" : "DH_ENABLE_15M"]: enabled ? "true" : "false" },
      `${window} 窗口已${enabled ? "开启" : "关闭"}`
    );

  const toggleAsset = (envKey: string, label: string, enabled: boolean) =>
    onPatch({ [envKey]: enabled ? "true" : "false" }, `${label} 已${enabled ? "开启" : "关闭"}`);

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 space-y-4">
      <h4 className="text-[11px] font-medium tracking-widest uppercase text-white/40">市场开关</h4>
      <p className="text-[12px] text-white/40 leading-relaxed">
        LIH 与 DH 共用 <code className="text-white/50">DH_ENABLE_*</code> 变量筛选可交易市场（5m BTC/ETH/SOL）。
      </p>

      <div className="space-y-3">
        <div className="flex items-center justify-between py-1">
          <Label htmlFor="mkt-5m" className="flex flex-col space-y-1">
            <span className="font-semibold text-white/90 text-[14px]">5 分钟窗口</span>
            <span className="font-normal text-white/40 text-[12px]">总开关</span>
          </Label>
          <Switch
            id="mkt-5m"
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
          <Label htmlFor="mkt-15m" className="flex flex-col space-y-1">
            <span className="font-semibold text-white/90 text-[14px]">15 分钟窗口</span>
            <span className="font-normal text-white/40 text-[12px]">总开关</span>
          </Label>
          <Switch
            id="mkt-15m"
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

      <div className="bg-white/5 p-3 rounded-lg border border-white/10 text-[12px] font-mono text-white/45">
        当前扫描 {live.marketsScanned} 个市场 · 5m{" "}
        {[live.dhEnable5mBtc && "BTC", live.dhEnable5mEth && "ETH", live.dhEnable5mSol && "SOL"]
          .filter(Boolean)
          .join(" · ") || "全关"}{" "}
        · 15m {[live.dhEnable15mBtc && "BTC", live.dhEnable15mEth && "ETH"].filter(Boolean).join(" · ") || "全关"}
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  const live = useLiveState();
  const lihMode = isLihPrimary(live);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [showLegacyDh, setShowLegacyDh] = useState(false);

  const [leg1Max, setLeg1Max] = useState("0.45");
  const [targetCombined, setTargetCombined] = useState("0.95");
  const [leg1Shares, setLeg1Shares] = useState("10");
  const [forceBalanceSecs, setForceBalanceSecs] = useState("45");
  const [maxMatched, setMaxMatched] = useState("50");
  const [maxRebalance, setMaxRebalance] = useState("0");
  const [lihMinRemaining, setLihMinRemaining] = useState("90");
  const [lihLeg1Cooldown, setLihLeg1Cooldown] = useState("20");
  const [lihRebalanceCooldown, setLihRebalanceCooldown] = useState("5");

  const [dhSumTarget, setDhSumTarget] = useState("0.95");
  const [dhMinDiscount, setDhMinDiscount] = useState("0.03");

  const tradingMode = deriveTradingMode(live);
  const controlsDisabled =
    loading || live.status === 2 || !live.botStreamConnected;

  const loadEnvConfig = useCallback(async () => {
    try {
      const res = await fetch("/api/bot/config");
      if (!res.ok) return;
      const data = (await res.json()) as { config?: Record<string, string> };
      const cfg = data.config ?? {};
      if (cfg.LIH_LEG1_SHARES) setLeg1Shares(cfg.LIH_LEG1_SHARES);
      if (cfg.LIH_FORCE_BALANCE_SECS) setForceBalanceSecs(cfg.LIH_FORCE_BALANCE_SECS);
      if (cfg.LIH_MAX_MATCHED_SHARES) setMaxMatched(cfg.LIH_MAX_MATCHED_SHARES);
      if (cfg.LIH_MAX_REBALANCE_SHARES) setMaxRebalance(cfg.LIH_MAX_REBALANCE_SHARES);
      if (cfg.LIH_MIN_SECONDS_REMAINING) setLihMinRemaining(cfg.LIH_MIN_SECONDS_REMAINING);
      if (cfg.LIH_LEG1_COOLDOWN_SECONDS) setLihLeg1Cooldown(cfg.LIH_LEG1_COOLDOWN_SECONDS);
      else if (cfg.LIH_COOLDOWN_SECONDS) setLihLeg1Cooldown(cfg.LIH_COOLDOWN_SECONDS);
      if (cfg.LIH_REBALANCE_COOLDOWN_SECONDS) setLihRebalanceCooldown(cfg.LIH_REBALANCE_COOLDOWN_SECONDS);
    } catch {
      /* live WS fields used as fallback */
    }
  }, []);

  useEffect(() => {
    setLeg1Max(live.lihLeg1MaxPrice.toFixed(2));
    setTargetCombined(live.lihTargetCombined.toFixed(2));
    setDhSumTarget(live.dhSumTarget.toFixed(3));
    setDhMinDiscount(live.dhMinDiscount.toFixed(3));
    loadEnvConfig();
  }, [
    live.lihLeg1MaxPrice,
    live.lihTargetCombined,
    live.dhSumTarget,
    live.dhMinDiscount,
    loadEnvConfig,
  ]);

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

  const applyTradingMode = async (mode: TradingMode) => {
    if (mode === tradingMode) return;
    if (mode === "live") {
      const ok = window.confirm(
        "确认开启实盘运行？\n\nBot 将向 Polymarket CLOB 发送真实订单并动用钱包资金。"
      );
      if (!ok) return;
    }
    setLoading(true);
    setMessage("");
    try {
      const patch: Record<string, string> = {};
      if (mode === "shadow") {
        patch.LIVE_LIH_DRY_RUN = "true";
      } else if (mode === "live") {
        patch.LIVE_LIH_DRY_RUN = "false";
      }
      if ((mode === "shadow" || mode === "live") && live.riskMaxConcurrentPositions <= 0) {
        const cfgRes = await fetch("/api/bot/config");
        const cfgData = cfgRes.ok
          ? ((await cfgRes.json()) as { config?: Record<string, string> })
          : { config: {} };
        const envMax = parseInt(cfgData.config?.RISK_MAX_CONCURRENT_POSITIONS ?? "0", 10);
        const restore = envMax > 0 ? envMax : 1;
        patch.RISK_MAX_CONCURRENT_POSITIONS = String(restore);
      }

      let body: Record<string, unknown>;
      if (mode === "stopped") {
        body = { action: "pause", reason: "Web: 停止新开仓" };
      } else if (mode === "shadow") {
        body = {
          patch,
          action: "resume",
          reason: "Web: Shadow 运行",
        };
      } else {
        body = {
          patch,
          action: "resume",
          reason: "Web: 实盘运行",
        };
      }
      const res = await fetch("/api/bot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "操作失败");
      setMessage(`已切换为：${TRADING_MODE_LABEL[mode]}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败");
    } finally {
      setLoading(false);
    }
  };

  const saveLihParams = () =>
    patchConfig(
      {
        LIH_LEG1_MAX_PRICE: leg1Max,
        LIH_TARGET_COMBINED: targetCombined,
        LIH_LEG1_SHARES: leg1Shares,
        LIH_FORCE_BALANCE_SECS: forceBalanceSecs,
        LIH_MAX_MATCHED_SHARES: maxMatched,
        LIH_MAX_REBALANCE_SHARES: maxRebalance,
        LIH_MIN_SECONDS_REMAINING: lihMinRemaining,
        LIH_LEG1_COOLDOWN_SECONDS: lihLeg1Cooldown,
        LIH_REBALANCE_COOLDOWN_SECONDS: lihRebalanceCooldown,
      },
      "LIH 参数已保存并热更新"
    );

  const saveDhParams = () =>
    patchConfig(
      {
        DH_SUM_TARGET: dhSumTarget,
        DH_MIN_DISCOUNT: dhMinDiscount,
      },
      "DH 遗留参数已保存（仅 LIH_ENABLED=false 时生效）"
    );

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader
          title="策略配置"
          description="LIH 分腿对冲 — 先买便宜腿，再 rebalance 配平。保存后写入 .env 并立即生效。"
          icon={SlidersHorizontal}
        />

        <div className="mb-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-2.5 text-[13px] text-emerald-200/90">
          主策略：<span className="font-mono font-bold">{lihMode ? "LIH" : "DH（遗留）"}</span>
          {" · "}
          当前：<span className="font-mono font-bold">{TRADING_MODE_LABEL[tradingMode]}</span>
          {live.statusReason && live.status !== 0 && (
            <span className="ml-2 text-white/50">（{live.statusReason}）</span>
          )}
          {!live.botStreamConnected && (
            <span className="ml-2 text-amber-200/90">Bot 未连接</span>
          )}
        </div>

        <GlassCard className="mb-5">
          <CardHeader className="pb-3">
            <CardTitle className="text-base font-semibold text-white/90">交易运行模式</CardTitle>
            <CardDescription className="text-white/40 text-[13px]">
              三档合一：停止 / Shadow（验簿不发单）/ 实盘（真下单）。写入 <code className="text-white/50">LIVE_LIH_DRY_RUN</code> 并同步运行状态。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {(["stopped", "shadow", "live"] as TradingMode[]).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant={tradingMode === mode ? "default" : "outline"}
                  disabled={controlsDisabled}
                  className={
                    tradingMode === mode
                      ? mode === "live"
                        ? "bg-emerald-600 hover:bg-emerald-500"
                        : mode === "shadow"
                          ? "bg-amber-600 hover:bg-amber-500"
                          : ""
                      : "border-white/15 bg-white/5 text-white/80"
                  }
                  onClick={() => void applyTradingMode(mode)}
                >
                  {TRADING_MODE_LABEL[mode]}
                </Button>
              ))}
            </div>
            <p className="mt-3 text-[12px] text-white/40 leading-relaxed">
              「停止」仅暂停新开仓，已有持仓保留。重启 bot 后默认暂停，需在下方选择 Shadow/实盘 并点「开始」才会交易（会自动清除 <code className="text-white/45">logs/STOP_TRADING</code>）。
            </p>
          </CardContent>
        </GlassCard>

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">
              分腿对冲 (LIH)
            </CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              先买低价单腿，等合价合适再补对腿；flex 模式支持稀释与 paired scale。风控单笔上限见「风控限额」页。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex items-center justify-between">
              <Label htmlFor="lih-enabled" className="flex flex-col space-y-1">
                <span className="font-semibold text-white/90 text-[14px]">启用 LIH (LIH_ENABLED)</span>
                <span className="font-normal text-white/40 text-[12px]">关闭后回退到 DH 检测器（遗留）。</span>
              </Label>
              <Switch
                id="lih-enabled"
                checked={live.lihEnabled}
                disabled={controlsDisabled}
                onCheckedChange={(checked) =>
                  patchConfig({ LIH_ENABLED: checked ? "true" : "false" }, checked ? "已启用 LIH" : "已切换为 DH 模式")
                }
              />
            </div>

            <div className="flex items-center justify-between py-1">
              <Label className="text-white/90 font-medium text-[14px]">使用 mirror 价 (LIH_USE_MIRROR)</Label>
              <Switch
                checked={live.lihUseMirror}
                disabled={controlsDisabled || !live.lihEnabled}
                onCheckedChange={(checked) =>
                  patchConfig({ LIH_USE_MIRROR: checked ? "true" : "false" }, "Mirror 设置已更新")
                }
              />
            </div>

            <MarketTogglesSection live={live} controlsDisabled={controlsDisabled} onPatch={patchConfig} />

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">Leg1 上限价 (LIH_LEG1_MAX_PRICE)</Label>
                <Input value={leg1Max} onChange={(e) => setLeg1Max(e.target.value)} className="font-mono bg-white/5 border-white/10" />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">目标合价 (LIH_TARGET_COMBINED)</Label>
                <Input
                  value={targetCombined}
                  onChange={(e) => setTargetCombined(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">Leg1 目标份额 (LIH_LEG1_SHARES)</Label>
                <Input
                  value={leg1Shares}
                  onChange={(e) => setLeg1Shares(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">强制配平窗口秒 (LIH_FORCE_BALANCE_SECS)</Label>
                <Input
                  value={forceBalanceSecs}
                  onChange={(e) => setForceBalanceSecs(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">最大 matched 份额 (LIH_MAX_MATCHED_SHARES)</Label>
                <Input
                  value={maxMatched}
                  onChange={(e) => setMaxMatched(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">Rebalance 份额上限 (0=仅预算)</Label>
                <Input
                  value={maxRebalance}
                  onChange={(e) => setMaxRebalance(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">窗口剩余秒数下限</Label>
                <Input
                  value={lihMinRemaining}
                  onChange={(e) => setLihMinRemaining(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">第一腿冷却 (LIH_LEG1_COOLDOWN_SECONDS)</Label>
                <Input
                  value={lihLeg1Cooldown}
                  onChange={(e) => setLihLeg1Cooldown(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-white/60 text-[12px]">配平/调仓冷却 (LIH_REBALANCE_COOLDOWN_SECONDS，0=关)</Label>
                <Input
                  value={lihRebalanceCooldown}
                  onChange={(e) => setLihRebalanceCooldown(e.target.value)}
                  className="font-mono bg-white/5 border-white/10"
                />
              </div>
            </div>

            <div className="bg-white/5 p-4 rounded-xl border border-white/10 text-[13px] text-white/50 space-y-1">
              <p>
                镜像资产：{live.mirrorAssetCount} {live.lihUseMirror ? "（mirror 优先）" : "（CLOB 订单簿）"}
              </p>
              <p className="text-white/35">
                Shadow 双策略：<code className="text-white/50">scripts/shadow_dual_run.sh</code>
              </p>
            </div>

            <div className="flex items-center justify-between pt-2">
              {message && <p className="text-[13px] text-amber-200/90">{message}</p>}
              <Button
                onClick={saveLihParams}
                disabled={loading || !live.lihEnabled}
                size="lg"
                variant="glass"
                className="ml-auto px-8 font-extrabold tracking-tight rounded-2xl"
              >
                {loading ? "保存中..." : "保存 LIH 参数"}
              </Button>
            </div>
          </CardContent>
        </GlassCard>

        <GlassCard className="mt-6 border-white/5">
          <CardHeader>
            <button
              type="button"
              className="flex w-full items-center justify-between text-left"
              onClick={() => setShowLegacyDh((v) => !v)}
            >
              <div className="flex items-center gap-2">
                <Archive className="h-4 w-4 text-white/30" />
                <CardTitle className="font-heading text-base font-semibold text-white/60">
                  遗留：Dump Hedge (DH)
                </CardTitle>
              </div>
              {showLegacyDh ? (
                <ChevronUp className="h-4 w-4 text-white/30" />
              ) : (
                <ChevronDown className="h-4 w-4 text-white/30" />
              )}
            </button>
            <CardDescription className="text-white/35 text-[12px]">
              已归档至 <code className="text-white/45">archive/dh-only/</code>。实盘抢单困难，默认不启用。设置{" "}
              <code className="text-white/45">LIH_ENABLED=false</code> 可恢复。
            </CardDescription>
          </CardHeader>
          {showLegacyDh && (
            <CardContent className="space-y-4 border-t border-white/5 pt-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label className="text-white/50 text-[12px]">合价目标 (DH_SUM_TARGET)</Label>
                  <Input
                    value={dhSumTarget}
                    onChange={(e) => setDhSumTarget(e.target.value)}
                    className="font-mono bg-white/5 border-white/10"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-white/50 text-[12px]">最小折价 (DH_MIN_DISCOUNT)</Label>
                  <Input
                    value={dhMinDiscount}
                    onChange={(e) => setDhMinDiscount(e.target.value)}
                    className="font-mono bg-white/5 border-white/10"
                  />
                </div>
              </div>
              <Button onClick={saveDhParams} disabled={loading} variant="outline" size="sm">
                保存 DH 参数
              </Button>
            </CardContent>
          )}
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
