/** Infer Polymarket up/down window from market title (fallback when backend omits windowMinutes). */
export function inferWindowMinutes(market: string, raw?: number): number {
  if (raw === 15) return 15;
  if (raw === 5) return 5;
  const q = market.toLowerCase();
  if (/15\s*min|15-minute|15-min|-\s*15m|\b15m\b/.test(q)) return 15;
  return 5;
}

export function tradeWindowLabel(minutes: number): string {
  return `${minutes}m`;
}

export function isHedgeTrade(strategy: string): boolean {
  return strategy === "DH" || strategy === "HEDGE";
}

export function resolveTradeWindow(market: string, windowMinutes?: number): number {
  return inferWindowMinutes(market, windowMinutes);
}
