"use client"

// The top tab bar shared by the lobby, leaderboard, stats and settings pages.
//
// Navigation is client-side on purpose (router.push, never a full page load): the session lives in
// memory only (transport/session.ts), so a hard navigation would log you out.

import { usePathname, useRouter } from "next/navigation"
import { cn } from "@/lib/utils"

const TABS = [
  { href: "/lobby", label: "大厅", icon: "♠", id: "lobby" },
  { href: "/leaderboard", label: "排行榜", icon: "♛", id: "leaderboard" },
  { href: "/stats", label: "数据", icon: "♦", id: "stats" },
  { href: "/settings", label: "设置", icon: "⚙", id: "settings" },
] as const

export default function AppNav({ className }: { className?: string }) {
  const router = useRouter()
  const pathname = usePathname()
  return (
    <nav
      aria-label="主导航"
      className={cn(
        "flex items-center justify-between gap-3 rounded-2xl border-2 border-primary/30 bg-card/90 px-3 py-2 shadow-xl backdrop-blur",
        className,
      )}
    >
      <span className="hidden items-center gap-2 pl-1 sm:flex">
        <span className="text-xl text-primary">♠</span>
        <span className="font-condensed text-lg font-semibold tracking-wide text-primary">POKER</span>
      </span>
      <div className="flex flex-1 items-center justify-center gap-1 sm:flex-none" role="tablist">
        {TABS.map((t) => {
          const active = pathname === t.href || pathname.startsWith(`${t.href}/`)
          return (
            <button
              key={t.href}
              type="button"
              role="tab"
              aria-selected={active}
              data-testid={`nav-${t.id}`}
              onClick={() => !active && router.push(t.href)}
              className={cn(
                "flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-semibold transition-colors sm:px-4",
                active
                  ? "bg-primary text-primary-foreground shadow-[0_6px_16px_-8px_rgba(212,175,55,0.8)]"
                  : "text-muted-foreground hover:bg-primary/10 hover:text-card-foreground",
              )}
            >
              <span aria-hidden className="text-base leading-none">
                {t.icon}
              </span>
              {t.label}
            </button>
          )
        })}
      </div>
    </nav>
  )
}
