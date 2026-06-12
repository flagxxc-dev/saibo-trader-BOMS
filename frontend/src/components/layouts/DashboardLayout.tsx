"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut } from "lucide-react";
import { signOut } from "next-auth/react";
import { APP_NAME } from "@/lib/branding";
import { getNavTitle, navigation } from "@/lib/navigation";

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex h-screen text-foreground selection:bg-amber-500/20 relative cockpit-bg">
      <aside className="w-64 panel flex flex-col z-20 relative rounded-none border-y-0 border-l-0 shrink-0">
        <div className="flex h-14 items-center px-5 border-b border-border/80">
          <span className="font-heading text-base font-extrabold tracking-tight text-gradient-accent">
            {APP_NAME}
          </span>
        </div>
        <nav className="flex-1 space-y-0.5 p-3 overflow-y-auto">
          {navigation.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[11px] font-bold tracking-[0.06em] uppercase transition-all ${
                  isActive
                    ? "bg-amber-500/12 text-amber-100 ring-1 ring-amber-500/25"
                    : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0 opacity-80" />
                <span className="flex-1">{item.name}</span>
                {item.demo && (
                  <span className="text-[8px] font-bold tracking-wider px-1 py-0.5 rounded bg-violet-500/20 text-violet-300 normal-case">
                    演示
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="p-3 border-t border-border/80">
          <button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm text-muted-foreground hover:bg-white/5 hover:text-foreground transition-all"
          >
            <LogOut className="h-4 w-4" />
            退出
          </button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden relative z-10 min-w-0">
        <header className="h-12 flex items-center justify-between px-6 sticky top-0 z-30 border-b border-border/60 bg-background/40 backdrop-blur-md">
          <h1 className="text-xs font-semibold tracking-wide text-muted-foreground">
            {getNavTitle(pathname)}
          </h1>
          <span className="font-mono text-[10px] text-muted-foreground/60">LIVE</span>
        </header>
        <main className="flex-1 overflow-y-auto p-5 md:p-6 relative">
          <div className="relative z-10 max-w-[1600px] mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
