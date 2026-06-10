export function coreStatusLabel(status: number): { text: string; color: string; pulse: boolean } {
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
