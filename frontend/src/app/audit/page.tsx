"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLiveState } from "@/hooks/useLiveState";

interface AuditEvent {
  ts: number;
  type: string;
  user?: string;
  patch?: Record<string, string>;
  action?: string;
  reason?: string;
}

function formatTs(ts: number) {
  return new Date(ts).toLocaleString("zh-CN", { hour12: false });
}

export default function AuditPage() {
  const live = useLiveState();
  const [configEvents, setConfigEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/bot/audit");
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "加载失败");
        setConfigEvents(data.events || []);
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "无法加载审计日志");
      }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);

  const runtimeEvents = useMemo(() => {
    const lines = [...live.telemetryLog, ...live.signalLog];
    return lines
      .filter((l) =>
        /CONFIG |REDEEM |SETTLED |SIGNAL |BALANCE |MARKETS |PAPER |LEGACY |FAIL|OPEN|DH SIGNAL/i.test(l)
      )
      .map((line, i) => ({
        id: `live-${i}-${line.slice(0, 24)}`,
        ts: live.timestamp,
        type: "runtime",
        summary: line,
      }));
  }, [live.telemetryLog, live.signalLog, live.timestamp]);

  const configRows = configEvents.map((e, i) => ({
    id: `cfg-${e.ts}-${i}`,
    ts: e.ts,
    type: e.type,
    summary:
      e.type === "config"
        ? `配置变更 ${Object.entries(e.patch || {}).map(([k, v]) => `${k}=${v}`).join(", ")}`
        : `控制 ${e.action}${e.reason ? ` — ${e.reason}` : ""}`,
    user: e.user as string | undefined,
  }));

  const runtimeRows = runtimeEvents.map((e) => ({
    id: e.id,
    ts: e.ts,
    type: e.type,
    summary: e.summary,
    user: undefined as string | undefined,
  }));

  const allEvents = [...configRows, ...runtimeRows].sort((a, b) => b.ts - a.ts).slice(0, 80);

  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader title="审计日志" description="配置变更与 bot 运行时事件。" icon={FileText} />

        {error && (
          <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-[13px] text-amber-200">
            {error}
          </div>
        )}

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">系统事件</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              Web 配置写入 logs/audit.jsonl；运行时事件来自 bot 遥测流。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {allEvents.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-16 border border-white/5 border-dashed rounded-2xl text-white/40 bg-white/[0.02]">
                <FileText className="h-12 w-12 mb-4 opacity-20" />
                <p className="text-[13px] tracking-wide">暂无审计事件。</p>
              </div>
            ) : (
              <ul className="divide-y divide-white/5 max-h-[70vh] overflow-y-auto">
                {allEvents.map((e) => (
                  <li key={e.id} className="py-3 px-1 flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] text-white/90 break-all">{e.summary}</p>
                      {e.user && (
                        <p className="text-[11px] text-white/35 mt-1">操作者: {e.user}</p>
                      )}
                    </div>
                    <div className="shrink-0 text-right">
                      <span className="text-[10px] uppercase tracking-wider text-violet-300/70">{e.type}</span>
                      <p className="text-[11px] font-mono text-white/40">{formatTs(e.ts)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
