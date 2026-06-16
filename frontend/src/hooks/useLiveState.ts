import { useState, useEffect } from "react";
import { inferWindowMinutes } from "@/lib/tradeWindow";

export interface MarketOpportunity {
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

/** @deprecated Use MarketOpportunity — kept for backward compatibility */
export type DHOpportunity = MarketOpportunity;

export interface TradeRecord {
  id: string;
  strategy: "LA" | "DH" | "LIH"; // LA = legacy history only
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
  lihPnl: number;
  winRate: number;
  openPositions: number;
  totalTrades: number;
  totalDhTrades: number;
  totalLihTrades: number;
  status: number;
  statusReason: string;
  isPaperMode: boolean;
  feeRate: number;
  feeModel: string;
  useDynamicFees: boolean;
  strategy: string;
  binanceFeedEnabled: boolean;
  dhSumTarget: number;
  dhMinDiscount: number;
  dhCooldownSeconds: number;
  dhMinSecondsRemaining: number;
  dhEnable5m: boolean;
  dhEnable15m: boolean;
  dhEnable5mBtc: boolean;
  dhEnable5mEth: boolean;
  dhEnable5mSol: boolean;
  dhEnable15mBtc: boolean;
  dhEnable15mEth: boolean;
  lihEnabled: boolean;
  lihDisableDh: boolean;
  lihLeg1MaxPrice: number;
  lihTargetCombined: number;
  lihUseMirror: boolean;
  liveLihDryRun?: boolean;
  tradesBaselineTs?: number;
  mirrorAssetCount: number;
  riskMaxPositionFraction: number;
  riskDailyLossLimit: number;
  riskTotalDrawdownKill: number;
  riskMaxConcurrentPositions: number;
  btcPrice: number;
  ethPrice: number;
  solPrice: number;
  fairValue: number;
  polymarketPrice: number;
  timestamp: number;
  marketsScanned: number;
  dhOpportunities: MarketOpportunity[];
  positionList: OpenPosition[];
  tradeHistory: TradeRecord[];
  telemetryLog: string[];
  signalLog: string[];
  /** false when WebSocket/SSE has not delivered a fresh frame recently */
  botStreamConnected: boolean;
  cashBalance?: number;
  positionsValue?: number;
  /** On-chain wallet total from fetch_balance.py (independent of paper sim balance) */
  realWalletBalance?: number;
  walletSource?: string;
}

const defaultState: LiveState = {
  balance: 0,
  startingBalance: 0,
  totalPnl: 0,
  dailyPnl: 0,
  laPnl: 0,
  dhPnl: 0,
  lihPnl: 0,
  winRate: 0,
  openPositions: 0,
  totalTrades: 0,
  totalDhTrades: 0,
  totalLihTrades: 0,
  status: 0,
  statusReason: "",
  isPaperMode: true,
  feeRate: 0.018,
  feeModel: "polymarket_v2_curve",
  useDynamicFees: false,
  strategy: "leg_in",
  binanceFeedEnabled: true,
  dhSumTarget: 0.95,
  dhMinDiscount: 0.02,
  dhCooldownSeconds: 30,
  dhMinSecondsRemaining: 60,
  dhEnable5m: true,
  dhEnable15m: true,
  dhEnable5mBtc: true,
  dhEnable5mEth: true,
  dhEnable5mSol: true,
  dhEnable15mBtc: true,
  dhEnable15mEth: true,
  lihEnabled: true,
  lihDisableDh: false,
  lihLeg1MaxPrice: 0.45,
  lihTargetCombined: 0.94,
  lihUseMirror: true,
  liveLihDryRun: true,
  mirrorAssetCount: 0,
  riskMaxPositionFraction: 0.08,
  riskDailyLossLimit: 0.2,
  riskTotalDrawdownKill: 0.4,
  riskMaxConcurrentPositions: 3,
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
  botStreamConnected: false,
};

const STREAM_STALE_MS = 8000;

function toNumber(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeWinRate(value: unknown): number {
  const n = toNumber(value, 0);
  const ratio = n > 1 ? n / 100 : n;
  return Math.min(Math.max(ratio, 0), 1);
}

/** WS may omit lihEnabled on older cores — infer from strategy, default LIH-first. */
function normalizeLihEnabled(raw: Record<string, unknown>): boolean {
  if (raw.lihEnabled === true) return true;
  if (raw.lihEnabled === false) return false;
  const strat = String(raw.strategy ?? "").toLowerCase();
  if (strat === "leg_in" || strat === "lih") return true;
  if (strat === "dump_hedge" || strat === "dh") return false;
  return true;
}

function normalizeOpportunities(value: unknown): MarketOpportunity[] {
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

function normalizePositions(value: unknown, opps: MarketOpportunity[], feeRate: number): OpenPosition[] {
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
    } else if (strategy === "LIH") {
      heldSide = heldSide ?? ((yesSize > 0 && noSize > 0) ? "BOTH" : yesSize > 0 ? "YES" : "NO");
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
    const strategyRaw = String(t.strategy ?? "DH").toUpperCase();
    const strategy: TradeRecord["strategy"] =
      strategyRaw === "LA" ? "LA" : strategyRaw === "LIH" ? "LIH" : "DH";
    const market = String(t.market ?? "");
    const windowMinutes = inferWindowMinutes(market, toNumber(t.windowMinutes) || undefined);
    return {
      id: String(t.id ?? ""),
      strategy,
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
  const dhOpportunities = normalizeOpportunities(raw.marketOpportunities ?? raw.dhOpportunities);
  const positionList = normalizePositions(raw.openPositions, dhOpportunities, feeRate);

  const openCount = typeof raw.openCount === "number" ? raw.openCount : positionList.length;

  return {
    balance: toNumber(raw.balance),
    startingBalance: toNumber(raw.startingBalance),
    totalPnl: toNumber(raw.totalPnl),
    dailyPnl: toNumber(raw.dailyPnl),
    laPnl: toNumber(raw.laPnl),
    dhPnl: toNumber(raw.dhPnl),
    lihPnl: toNumber(raw.lihPnl),
    winRate: normalizeWinRate(raw.winRate),
    openPositions: openCount,
    totalTrades: toNumber(raw.totalTrades),
    totalDhTrades: toNumber(raw.totalDhTrades),
    totalLihTrades: toNumber(raw.totalLihTrades),
    status: toNumber(raw.status),
    statusReason: String(raw.statusReason ?? ""),
    isPaperMode: raw.isPaperMode !== false,
    feeRate,
    feeModel: String(raw.feeModel ?? "polymarket_v2_curve"),
    useDynamicFees: raw.useDynamicFees === true,
    strategy: String(raw.strategy ?? "leg_in"),
    binanceFeedEnabled: raw.binanceFeedEnabled !== false,
    dhSumTarget: toNumber(raw.dhSumTarget, 0.95) || 0.95,
    dhMinDiscount: toNumber(raw.dhMinDiscount, 0.02) || 0.02,
    dhCooldownSeconds: toNumber(raw.dhCooldownSeconds, 30) || 30,
    dhMinSecondsRemaining: toNumber(raw.dhMinSecondsRemaining, 60) || 60,
    dhEnable5m: raw.dhEnable5m !== false,
    dhEnable15m: raw.dhEnable15m !== false,
    dhEnable5mBtc: raw.dhEnable5mBtc !== false,
    dhEnable5mEth: raw.dhEnable5mEth !== false,
    dhEnable5mSol: raw.dhEnable5mSol !== false,
    dhEnable15mBtc: raw.dhEnable15mBtc !== false,
    dhEnable15mEth: raw.dhEnable15mEth !== false,
    lihEnabled: normalizeLihEnabled(raw),
    lihDisableDh: raw.lihDisableDh === true,
    lihLeg1MaxPrice: toNumber(raw.lihLeg1MaxPrice, 0.45) || 0.45,
    lihTargetCombined: toNumber(raw.lihTargetCombined, 0.94) || 0.94,
    lihUseMirror: raw.lihUseMirror !== false,
    liveLihDryRun: raw.isPaperMode === false ? raw.liveLihDryRun !== false : undefined,
    tradesBaselineTs: toNumber(raw.tradesBaselineTs, 0) || undefined,
    mirrorAssetCount: toNumber(raw.mirrorAssetCount, 0) || 0,
    riskMaxPositionFraction: toNumber(raw.riskMaxPositionFraction, 0.08) || 0.08,
    riskDailyLossLimit: toNumber(raw.riskDailyLossLimit, 0.2) || 0.2,
    riskTotalDrawdownKill: toNumber(raw.riskTotalDrawdownKill, 0.4) || 0.4,
    riskMaxConcurrentPositions: toNumber(raw.riskMaxConcurrentPositions, 3) || 3,
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
    botStreamConnected: true,
    cashBalance: toNumber(raw.cashBalance, 0) || undefined,
    positionsValue: toNumber(raw.positionsValue, 0) || undefined,
    realWalletBalance: toNumber(raw.realWalletBalance, 0) || undefined,
    walletSource: typeof raw.walletSource === "string" ? raw.walletSource : undefined,
  };
}

export function useLiveState() {
  const [state, setState] = useState<LiveState>(defaultState);
  const [lastFrameAt, setLastFrameAt] = useState(0);

  useEffect(() => {
    const eventSource = new EventSource("/api/live");

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        const now = Date.now();
        setLastFrameAt(now);
        setState({ ...normalizeLiveState(data), botStreamConnected: true });
      } catch (err) {
        console.error("Failed to parse live state:", err);
      }
    };

    eventSource.onerror = () => {
      console.warn("Live state stream interrupted");
      setState((prev) => ({
        ...prev,
        botStreamConnected: false,
        status: 3,
        statusReason: prev.statusReason || "Bot 数据流中断",
      }));
    };

    return () => {
      eventSource.close();
    };
  }, []);

  useEffect(() => {
    const tick = window.setInterval(() => {
      if (!lastFrameAt) return;
      if (Date.now() - lastFrameAt > STREAM_STALE_MS) {
        setState((prev) =>
          prev.botStreamConnected
            ? {
                ...prev,
                botStreamConnected: false,
                status: 3,
                statusReason: "Bot 未连接或数据过期",
              }
            : prev
        );
      }
    }, 2000);
    return () => window.clearInterval(tick);
  }, [lastFrameAt]);

  return state;
}
