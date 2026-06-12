import { Activity, ShieldAlert, SlidersHorizontal, History, FileText, LucideIcon } from "lucide-react";

export interface NavItem {
  name: string;
  href: string;
  icon: LucideIcon;
  demo?: boolean;
}

export const navigation: NavItem[] = [
  { name: "仪表盘", href: "/dashboard", icon: Activity },
  { name: "交易历史", href: "/history", icon: History },
  { name: "策略配置", href: "/strategies", icon: SlidersHorizontal },
  { name: "风控限额", href: "/risk", icon: ShieldAlert },
  { name: "审计日志", href: "/audit", icon: FileText },
];

export function getNavTitle(pathname: string): string {
  return navigation.find((item) => item.href === pathname)?.name ?? "仪表盘";
}
