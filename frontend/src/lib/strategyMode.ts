import type { LiveState } from "@/hooks/useLiveState";

function closedLihRounds(state: LiveState): number {
  const fromHistory = state.tradeHistory.filter((r) => r.strategy === "LIH" && r.status === "closed").length;
  return Math.max(state.totalLihTrades, fromHistory);
}

function closedDhRounds(state: LiveState): number {
  const fromHistory = state.tradeHistory.filter((r) => r.strategy === "DH" && r.status === "closed").length;
  return Math.max(state.totalDhTrades, fromHistory);
}

/** Primary strategy: LIH unless bot explicitly reports dump_hedge with no LIH activity. */
export function isLihPrimary(state: Pick<LiveState, "lihEnabled" | "strategy" | "tradeHistory">): boolean {
  if (state.lihEnabled) return true;
  const strat = (state.strategy || "").toLowerCase();
  if (strat === "leg_in" || strat === "lih") return true;
  const hasLihActivity = state.tradeHistory.some((r) => r.strategy === "LIH");
  if (hasLihActivity) return true;
  if (strat === "dump_hedge" || strat === "dh") return false;
  return true;
}

export function strategyShortLabel(state: Pick<LiveState, "lihEnabled" | "strategy" | "tradeHistory">): string {
  return isLihPrimary(state) ? "LIH" : "DH";
}

/** Closed-round counter from bot state + trade history. */
export function cumulativeClosedTrades(state: LiveState): number {
  return isLihPrimary(state) ? closedLihRounds(state) : closedDhRounds(state);
}

export function strategyRealizedPnl(state: LiveState): number {
  return isLihPrimary(state) ? state.lihPnl : state.dhPnl;
}
