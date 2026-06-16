/** Matches C++ risk::TradingStatus */
export const CORE_STATUS = {
  ACTIVE: 0,
  DAILY_HALT: 1,
  KILLED: 2,
  PAUSED: 3,
} as const;

/** Only ACTIVE (0) with a live bot stream counts as “running”. */
export function isBotTradingActive(status: number, streamConnected: boolean): boolean {
  return streamConnected && status === CORE_STATUS.ACTIVE;
}

export function coreStatusLabel(
  status: number,
  streamConnected = true
): { text: string; color: string; pulse: boolean } {
  if (!streamConnected) {
    return { text: "Bot 未连接", color: "text-white/40", pulse: false };
  }
  switch (status) {
    case 0:
      return { text: "运行中", color: "text-emerald-400", pulse: true };
    case 1:
      return { text: "日限额暂停（UTC 午夜重置）", color: "text-amber-400", pulse: false };
    case 2:
      return { text: "熔断停止", color: "text-red-400", pulse: false };
    case 3:
      return { text: "短暂暂停", color: "text-amber-400", pulse: false };
    default:
      return { text: "未知", color: "text-white/40", pulse: false };
  }
}
