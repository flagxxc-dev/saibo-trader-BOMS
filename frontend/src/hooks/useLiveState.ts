import { useState, useEffect } from "react";
import { inferWindowMinutes } from "@/lib/tradeWindow";

export interface DHOpportunity {
  question: string;
  asset: string;
  windowMinutes?: number;
  yesPrice: number;
  noPrice: number;
  combined: number;
  discount: number;
  discountPct: number;
  endDate: string;
  endDateTs?: number;
}

export interface TradeRecord {
  id: string;
  strategy: "LA" | "DH";
  asset: string;
  status: "open" | "closed";
  market: string;
  side: string;
  direction?: string;
  entryPrice: number;
  exitPrice?: number;
  yesEntryPrice?: number;
  noEntryPrice?: number;
  yesExitPrice?: number;
  noExitPrice?: number;
  size: number;
  costUsdc: number;
  entryFee: number;
  exitFee: number;
  lockedProfit?: number;
  pnlUsdc: number;
  openedAt: number;
  closedAt?: number;
  endDateTs?: number;
  exitReason?: string;
  isPaperMode: boolean;
  windowMinutes?: number;
}

export interface OpenPosition {
  asset: string;
  side: string;
  entryPrice: number;
  size: number;
  cost: number;
  strategy: string;
  question: string;
  pnl: number;
  direction?: string;
  heldSide?: "YES" | "NO" | "BOTH";
  endDateTs?: number;
  windowMinutes?: number;
  yesEntryPrice?: number;
  noEntryPrice?: number;
  yesLivePrice?: number;
  noLivePrice?: number;
  yesSize?: number;
  noSize?: number;
  yesCost?: number;
  noCost?: number;
  entryFee?: number;
}

export interface LiveState {
  balance: number;
  startingBalance: number;
  totalPnl: number;
  dailyPnl: number;
  laPnl: number;
  dhPnl: number;
  winRate: number;
  openPositions: number;
  totalTrades: number;
  totalDhTrades: number;
  status: number;
  statusReason: string;
  isPaperMode: boolean;
  feeRate: number;
  strategy: string;
  binanceFeedEnabled: boolean;
  dhSumTarget: number;
  dhMinDiscount: number;
  btcPrice: number;
  ethPrice: number;
  solPrice: number;
  fairValue: number;
  polymarketPrice: number;
  timestamp: number;
  marketsScanned: number;
  dhOpportunities: DHOpportunity[];
  positionList: OpenPosition[];
  tradeHistory: TradeRecord[];
  telemetryLog: string[];
  signalLog: string[];
}

const defaultState: LiveState = {
  balance: 0,
  startingBalance: 0,
  totalPnl: 0,
  dailyPnl: 0,
  laPnl: 0,
  dhPnl: 0,
  winRate: 0,
  openPositions: 0,
  totalTrades: 0,
  totalDhTrades: 0,
  status: 0,
  statusReason: "",
  isPaperMode: true,
  feeRate: 0.018,
  strategy: "dump_hedge",
  binanceFeedEnabled: true,
  dhSumTarget: 0.95,
  dhMinDiscount: 0.02,
  btcPrice: 0,
  ethPrice: 0,
  solPrice: 0,
  fairValue: 0,
  polymarketPrice: 0,
  timestamp: 0,
  marketsScanned: 0,
  dhOpportunities: [],
  positionList: [],
  tradeHistory: [],
  telemetryLog: [],
  signalLog: [],
};

function toNumber(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeWinRate(value: unknown): number {
  const n = toNumber(value, 0);
  return n > 1 ? n / 100 : n;
}

function normalizeOpportunities(value: unknown): DHOpportunity[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const opp = item as Record<string, unknown>;
    const yesPrice = toNumber(opp.yesPrice);
    const noPrice = toNumber(opp.noPrice);
    const combined = toNumber(opp.combined, yesPrice + noPrice);
    return {
      question: String(opp.question ?? ""),
      asset: String(opp.asset ?? ""),
      windowMinutes: toNumber(opp.windowMinutes, 5) || 5,
      yesPrice,
      noPrice,
      combined,
      discount: toNumber(opp.discount),
      discountPct: toNumber(opp.discountPct),
      endDate: String(opp.endDate ?? ""),
      endDateTs: toNumber(opp.endDateTs) || undefined,
    };
  });
}

function normalizePositions(value: unknown, opps: DHOpportunity[], feeRate: number): OpenPosition[] {
  if (!Array.isArray(value)) return [];

  return value.map((item) => {
    const p = item as Record<string, unknown>;
    const strategy = String(p.strategy ?? "");
    const question = String(p.question ?? "");
    const asset = String(p.asset ?? "");
    const windowMinutes = inferWindowMinutes(
      question,
      toNumber(p.windowMinutes) || undefined
    );
    const entryPrice = toNumber(p.entryPrice);
    const size = toNumber(p.size);
    const cost = toNumber(p.cost);

    const matched = opps.find((o) => o.question === question) ?? opps.find((o) => o.asset === asset);

    const yesLivePrice = toNumber(p.yesLivePrice, matched?.yesPrice ?? 0);
    const noLivePrice = toNumber(p.noLivePrice, matched?.noPrice ?? 0);
    const endDateTs = toNumber(p.endDateTs, matched?.endDateTs ?? 0) || undefined;

    let yesEntryPrice = toNumber(p.yesEntryPrice);
    let noEntryPrice = toNumber(p.noEntryPrice);
    let yesSize = toNumber(p.yesSize);
    let noSize = toNumber(p.noSize);
    let yesCost = toNumber(p.yesCost);
    let noCost = toNumber(p.noCost);
    let heldSide = p.heldSide as OpenPosition["heldSide"] | undefined;

    if (strategy === "DH") {
      if (!yesEntryPrice && !noEntryPrice && entryPrice > 0 && matched && matched.combined > 0) {
        yesEntryPrice = entryPrice * (matched.yesPrice / matched.combined);
        noEntryPrice = entryPrice * (matched.noPrice / matched.combined);
      }
      yesSize = yesSize || size;
      noSize = noSize || size;
      yesCost = yesCost || yesEntryPrice * yesSize;
      noCost = noCost || noEntryPrice * noSize;
      heldSide = heldSide ?? "BOTH";
    } else {
      const isYes = String(p.direction ?? p.side ?? "").toUpperCase().includes("YES") || String(p.side).toUpperCase() === "UP";
      heldSide = heldSide ?? (isYes ? "YES" : "NO");
      if (heldSide === "YES") {
        yesEntryPrice = yesEntryPrice || entryPrice;
        yesSize = yesSize || size;
        yesCost = yesCost || cost;
      } else {
        noEntryPrice = noEntryPrice || entryPrice;
        noSize = noSize || size;
        noCost = noCost || cost;
      }
    }

    return {
      asset,
      side: String(p.side ?? ""),
      entryPrice,
      size,
      cost,
      strategy,
      question,
      windowMinutes,
      pnl: toNumber(p.pnl),
      direction: String(p.direction ?? ""),
      heldSide,
      endDateTs,
      yesEntryPrice,
      noEntryPrice,
      yesLivePrice,
      noLivePrice,
      yesSize,
      noSize,
      yesCost,
      noCost,
      entryFee: toNumber(p.entryFee, cost * feeRate),
    };
  });
}

function normalizeTradeHistory(value: unknown): TradeRecord[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const t = item as Record<string, unknown>;
    const strategy = String(t.strategy ?? "LA") as "LA" | "DH";
    const market = String(t.market ?? "");
    const windowMinutes = inferWindowMinutes(market, toNumber(t.windowMinutes) || undefined);
    return {
      id: String(t.id ?? ""),
      strategy: strategy === "DH" ? "DH" : "LA",
      asset: String(t.asset ?? ""),
      windowMinutes,
      status: String(t.status ?? "closed") === "open" ? "open" : "closed",
      market,
      side: String(t.side ?? ""),
      direction: String(t.direction ?? ""),
      entryPrice: toNumber(t.entryPrice),
      exitPrice: toNumber(t.exitPrice) || undefined,
      yesEntryPrice: toNumber(t.yesEntryPrice) || undefined,
      noEntryPrice: toNumber(t.noEntryPrice) || undefined,
      yesExitPrice: toNumber(t.yesExitPrice) || undefined,
      noExitPrice: toNumber(t.noExitPrice) || undefined,
      size: toNumber(t.size),
      costUsdc: toNumber(t.costUsdc),
      entryFee: toNumber(t.entryFee),
      exitFee: toNumber(t.exitFee),
      lockedProfit: toNumber(t.lockedProfit) || undefined,
      pnlUsdc: toNumber(t.pnlUsdc),
      openedAt: toNumber(t.openedAt),
      closedAt: toNumber(t.closedAt) || undefined,
      endDateTs: toNumber(t.endDateTs) || undefined,
      exitReason: String(t.exitReason ?? ""),
      isPaperMode: t.isPaperMode !== false,
    };
  });
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((v) => String(v));
}

function normalizeLiveState(raw: Record<string, unknown>): LiveState {
  const feeRate = toNumber(raw.feeRate, 0.018) || 0.018;
  const dhOpportunities = normalizeOpportunities(raw.dhOpportunities);
  const positionList = normalizePositions(raw.openPositions, dhOpportunities, feeRate);

  const openCount = typeof raw.openCount === "number" ? raw.openCount : positionList.length;

  return {
    balance: toNumber(raw.balance),
    startingBalance: toNumber(raw.startingBalance),
    totalPnl: toNumber(raw.totalPnl),
    dailyPnl: toNumber(raw.dailyPnl),
    laPnl: toNumber(raw.laPnl),
    dhPnl: toNumber(raw.dhPnl),
    winRate: normalizeWinRate(raw.winRate),
    openPositions: openCount,
    totalTrades: toNumber(raw.totalTrades),
    totalDhTrades: toNumber(raw.totalDhTrades),
    status: toNumber(raw.status),
    statusReason: String(raw.statusReason ?? ""),
    isPaperMode: raw.isPaperMode !== false,
    feeRate,
    strategy: String(raw.strategy ?? "dump_hedge"),
    binanceFeedEnabled: raw.binanceFeedEnabled !== false,
    dhSumTarget: toNumber(raw.dhSumTarget, 0.95) || 0.95,
    dhMinDiscount: toNumber(raw.dhMinDiscount, 0.02) || 0.02,
    btcPrice: toNumber(raw.btcPrice),
    ethPrice: toNumber(raw.ethPrice),
    solPrice: toNumber(raw.solPrice),
    fairValue: toNumber(raw.fairValue),
    polymarketPrice: toNumber(raw.polymarketPrice),
    timestamp: toNumber(raw.timestamp, Date.now()),
    marketsScanned: toNumber(raw.marketsScanned, dhOpportunities.length),
    dhOpportunities,
    positionList,
    tradeHistory: normalizeTradeHistory(raw.tradeHistory),
    telemetryLog: normalizeStringArray(raw.telemetryLog),
    signalLog: normalizeStringArray(raw.signalLog),
  };
}

export function useLiveState() {
  const [state, setState] = useState<LiveState>(defaultState);

  useEffect(() => {
    const eventSource = new EventSource("/api/live");

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        setState(normalizeLiveState(data));
      } catch (err) {
        console.error("Failed to parse live state:", err);
      }
    };

    eventSource.onerror = () => {
      console.warn("Live state stream interrupted");
    };

    return () => {
      eventSource.close();
    };
  }, []);

  return state;
}
