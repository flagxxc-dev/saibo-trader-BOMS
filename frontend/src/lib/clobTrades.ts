import type { TradeRecord } from "@/hooks/useLiveState";

export interface ClobTradeFill {
  id: string;
  orderID?: string;
  tokenID?: string;
  side: string;
  size: number;
  price: number;
  usdcSize: number;
  timestamp: number;
  outcome?: string;
  title: string;
  asset: string;
  windowMinutes?: number | null;
  transactionHash?: string;
}

/** Polymarket activity timestamps are seconds; bot state uses seconds too. */
function normalizeTs(ts: number): number {
  if (!ts || ts <= 0) return 0;
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

export function clobFillToTradeRecord(fill: ClobTradeFill): TradeRecord {
  const ts = normalizeTs(fill.timestamp);
  const cost = fill.usdcSize > 0 ? fill.usdcSize : fill.price * fill.size;
  const side = fill.side || "BUY";
  const outcome = fill.outcome ? ` ${fill.outcome}` : "";
  return {
    id: fill.id || fill.orderID || fill.transactionHash || `clob-${ts}`,
    strategy: "LIH",
    asset: fill.asset || "—",
    status: "closed",
    market: fill.title || "Polymarket",
    side: `${side}${outcome}`.trim(),
    direction: "CLOB",
    entryPrice: fill.price,
    exitPrice: fill.price,
    size: fill.size,
    costUsdc: cost,
    entryFee: 0,
    exitFee: 0,
    pnlUsdc: 0,
    openedAt: ts,
    closedAt: ts,
    exitReason: "CLOB_FILL",
    isPaperMode: false,
    windowMinutes: fill.windowMinutes ?? undefined,
  };
}

export function mergeTradeHistory(
  botRecords: TradeRecord[],
  clobFills: ClobTradeFill[],
  baselineTs = 0
): TradeRecord[] {
  const afterBaseline = (ts: number) => baselineTs <= 0 || ts <= 0 || ts >= baselineTs;
  const filteredBot = botRecords.filter((r) => afterBaseline(r.closedAt || r.openedAt || 0));
  const seen = new Set(filteredBot.map((r) => r.id));
  const merged = [...filteredBot];
  for (const fill of clobFills) {
    const rec = clobFillToTradeRecord(fill);
    if (!afterBaseline(rec.closedAt || rec.openedAt || 0)) continue;
    if (seen.has(rec.id)) continue;
    seen.add(rec.id);
    merged.push(rec);
  }
  merged.sort((a, b) => {
    const ta = a.closedAt || a.openedAt || 0;
    const tb = b.closedAt || b.openedAt || 0;
    return tb - ta;
  });
  return merged;
}
