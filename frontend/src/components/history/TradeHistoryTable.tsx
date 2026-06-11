"use client";

import type { TradeRecord } from "@/hooks/useLiveState";
import { isHedgeTrade, resolveTradeWindow, tradeWindowLabel } from "@/lib/tradeWindow";

function fmtTime(ts: number): string {
  if (!ts || ts <= 0) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

function windowBadgeClass(minutes: number): string {
  return minutes === 15
    ? "bg-indigo-500/20 text-indigo-300"
    : "bg-purple-500/20 text-purple-300";
}

export function TradeHistoryTable({ records, emptyText }: { records: TradeRecord[]; emptyText?: string }) {
  if (records.length === 0) {
    return (
      <div className="py-16 text-center text-white/30 text-xs font-mono border border-dashed border-white/10 rounded-2xl">
        {emptyText ?? "暂无交易记录"}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-white/5">
      <table className="w-full text-left min-w-[960px]">
        <thead className="text-[9px] text-white/25 uppercase font-mono border-b border-white/5 bg-white/[0.02]">
          <tr>
            <th className="px-3 py-2.5">时间</th>
            <th className="px-3 py-2.5">模式</th>
            <th className="px-3 py-2.5">窗口</th>
            <th className="px-3 py-2.5">标的</th>
            <th className="px-3 py-2.5">市场</th>
            <th className="px-3 py-2.5 text-right">入场价</th>
            <th className="px-3 py-2.5 text-right">出场价</th>
            <th className="px-3 py-2.5 text-right">份额</th>
            <th className="px-3 py-2.5 text-right">成本</th>
            <th className="px-3 py-2.5 text-right">手续费</th>
            <th className="px-3 py-2.5 text-right">盈亏</th>
            <th className="px-3 py-2.5">状态</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5 font-mono text-[11px]">
          {records.map((r) => {
            const isOpen = r.status === "open";
            const pnlPositive = r.pnlUsdc >= 0;
            const totalFee = r.entryFee + r.exitFee;
            const windowMin = resolveTradeWindow(r.market, r.windowMinutes);
            const hedge = isHedgeTrade(r.strategy) || (r.yesEntryPrice != null && r.noEntryPrice != null);
            const entryDetail =
              hedge && r.yesEntryPrice && r.noEntryPrice
                ? `Y${r.yesEntryPrice.toFixed(3)}/N${r.noEntryPrice.toFixed(3)}`
                : r.entryPrice.toFixed(4);
            const exitDetail =
              hedge && r.yesExitPrice != null && r.noExitPrice != null && !isOpen
                ? `Y${r.yesExitPrice.toFixed(3)}/N${r.noExitPrice.toFixed(3)}`
                : r.exitPrice != null && r.exitPrice > 0
                  ? r.exitPrice.toFixed(4)
                  : "—";

            return (
              <tr key={r.id} className="hover:bg-white/[0.02] align-top">
                <td className="px-3 py-2.5 text-white/50 whitespace-nowrap">
                  {fmtTime(isOpen ? r.openedAt : r.closedAt || r.openedAt)}
                </td>
                <td className="px-3 py-2.5">
                  <span
                    className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${
                      r.isPaperMode ? "bg-amber-500/20 text-amber-300" : "bg-emerald-500/20 text-emerald-300"
                    }`}
                  >
                    {r.isPaperMode ? "纸面" : "实盘"}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  <span
                    className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${windowBadgeClass(windowMin)}`}
                  >
                    {tradeWindowLabel(windowMin)}
                  </span>
                  <span className="text-white/30 ml-1 text-[10px]">
                    {hedge ? "折价对冲" : "延迟套利"}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-white/80 uppercase font-bold">{r.asset}</td>
                <td className="px-3 py-2.5 text-white/45 max-w-[200px] truncate" title={r.market}>
                  {r.market || "—"}
                </td>
                <td className="px-3 py-2.5 text-right text-white/70" title={entryDetail}>
                  {entryDetail}
                </td>
                <td className="px-3 py-2.5 text-right text-white/70" title={exitDetail}>
                  {exitDetail}
                </td>
                <td className="px-3 py-2.5 text-right text-white/60">{r.size.toFixed(2)}</td>
                <td className="px-3 py-2.5 text-right text-white/60">{fmtUsd(r.costUsdc)}</td>
                <td className="px-3 py-2.5 text-right text-white/40">
                  {fmtUsd(totalFee)}
                  <span className="block text-[9px] text-white/25">
                    入{fmtUsd(r.entryFee)}
                    {!isOpen && r.exitFee > 0 ? ` 出${fmtUsd(r.exitFee)}` : ""}
                  </span>
                </td>
                <td className={`px-3 py-2.5 text-right font-bold ${pnlPositive ? "text-emerald-400" : "text-red-400"}`}>
                  {isOpen ? "浮动 " : ""}
                  {pnlPositive ? "+" : ""}
                  {fmtUsd(r.pnlUsdc)}
                </td>
                <td className="px-3 py-2.5">
                  {isOpen ? (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 font-bold">持仓中</span>
                  ) : (
                    <span className="text-[9px] text-white/40">{r.exitReason || "已平仓"}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
