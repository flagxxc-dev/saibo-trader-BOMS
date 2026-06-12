"use client";

import { useEffect, useState } from "react";

interface PreflightCheck {
  name: string;
  ok: boolean;
  detail?: string;
}

interface PreflightReport {
  ok?: boolean;
  mode?: string;
  ts?: string;
  checks?: PreflightCheck[];
  warnings?: string[];
  fee_model?: {
    env_FEE_RATE_flat?: number;
    v2_order_json_includes_feeRateBps?: boolean;
    sample?: {
      dynamic_fee_per_share?: number;
      flat_fee_per_share?: number;
      discount_dynamic_pct?: number;
      discount_flat_pct?: number;
    };
  };
  env_mapping?: {
    maker_address?: string;
    signer_address?: string;
    signatureType?: number;
    verifyingContract_neg_risk_updown?: string;
  };
  live_first_order_checklist?: string[];
}

export function PreflightBanner() {
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    fetch("/api/bot/preflight")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setReport(data?.preflight ?? null))
      .catch(() => setReport(null));
  }, []);

  if (!report) return null;

  const mode = (report.mode || "paper").toUpperCase();
  const fee = report.fee_model;
  const mapping = report.env_mapping;
  const failed = (report.checks || []).filter((c) => !c.ok);

  return (
    <div
      className={`rounded-lg border px-4 py-3 text-[13px] ${
        report.ok
          ? "border-emerald-500/25 bg-emerald-500/8 text-emerald-50/90"
          : "border-red-500/30 bg-red-500/10 text-red-50/90"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <strong>启动自检</strong>
          <span className="ml-2 opacity-80">模式 {mode}</span>
          {fee?.sample && (
            <span className="ml-2 opacity-80">
              动态费≈{(fee.sample.dynamic_fee_per_share ?? 0).toFixed(4)}/份 vs 扁平≈
              {(fee.sample.flat_fee_per_share ?? 0).toFixed(4)}/份
            </span>
          )}
        </div>
        <button
          type="button"
          className="text-xs underline opacity-70 hover:opacity-100"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起" : "详情"}
        </button>
      </div>

      {failed.length > 0 && (
        <ul className="mt-2 space-y-1">
          {failed.map((c) => (
            <li key={c.name}>
              ✗ {c.name}
              {c.detail ? ` — ${c.detail}` : ""}
            </li>
          ))}
        </ul>
      )}

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-white/10 pt-3 text-xs opacity-90">
          {mapping && (
            <p>
              Maker {mapping.maker_address || "—"} · Signer {mapping.signer_address || "—"} · SigType{" "}
              {mapping.signatureType ?? "—"} · NegRisk {mapping.verifyingContract_neg_risk_updown || "—"}
            </p>
          )}
          {fee && (
            <p>
              V2 下单 JSON 不含 feeRateBps（{String(fee.v2_order_json_includes_feeRateBps)}）；纸面/信号用 CLOB fd.r/e
              曲线，无 API 时回退 FEE_RATE={(Number(fee.env_FEE_RATE_flat) * 100).toFixed(2)}%
            </p>
          )}
          {(report.warnings || []).map((w) => (
            <p key={w}>• {w}</p>
          ))}
          {report.live_first_order_checklist && report.live_first_order_checklist.length > 0 && (
            <ol className="list-decimal pl-4 space-y-0.5">
              {report.live_first_order_checklist.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
