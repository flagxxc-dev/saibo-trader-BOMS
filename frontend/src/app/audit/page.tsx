"use client";

import { DashboardLayout } from "@/components/layouts/DashboardLayout";
import { PageContainer } from "@/components/shared/PageContainer";
import { PageHeader } from "@/components/shared/PageHeader";
import { DemoBanner } from "@/components/shared/DemoBanner";
import { GlassCard, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/shared/GlassCard";
import { FileText } from "lucide-react";

export default function AuditPage() {
  return (
    <DashboardLayout>
      <PageContainer>
        <PageHeader title="审计日志" description="参数变更与系统事件（演示）。" icon={FileText} />

        <DemoBanner hint="审计功能尚未接入，后续可记录 .env 变更与容器重启事件。" />

        <GlassCard>
          <CardHeader>
            <CardTitle className="font-heading text-lg font-semibold tracking-tight text-gradient">系统事件</CardTitle>
            <CardDescription className="text-white/40 text-[13px] leading-relaxed">
              经认证的系统修改将显示在此。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col items-center justify-center p-16 border border-white/5 border-dashed rounded-2xl text-white/40 bg-white/[0.02]">
              <FileText className="h-12 w-12 mb-4 opacity-20" />
              <p className="text-[13px] tracking-wide">暂无审计事件。</p>
            </div>
          </CardContent>
        </GlassCard>
      </PageContainer>
    </DashboardLayout>
  );
}
