"use client"

import { useEffect, useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import AppNav from "@/components/AppNav"
import Avatar from "@/components/Avatar"
import { cn } from "@/lib/utils"
import {
  fetchLeaderboard,
  fetchLeaderboardStats,
  fetchProfile,
  type LeaderboardEntry,
  type Rival,
  type StatsEntry,
} from "@/transport/rest"
import { getSession } from "@/transport/session"
import { useRoom } from "@/store/useRoom"

/**
 * 一次拉多少条。对应后端 `LEADERBOARD_MAX_LIMIT`(service/app/poker.env.example)。
 *
 * **这是一份手抄的后端配置**,只能如此:REST DTO 与其边界没有 codegen —— 0094 把每个端点收进信封之后,
 * OpenAPI 里只剩 `SecureRequest`/`SecureResponse`,真正的形状在密文内层(见 service/docs/rest.md 共同原则 4)。
 * 别往上加:0094 起 `limit` 越界是 **400**,不是默默截断。
 */
const LEADERBOARD_PAGE_SIZE = 100

type Tab = "total" | "today" | "points"

const TABS: { id: Tab; label: string }[] = [
  { id: "total", label: "总榜" },
  { id: "today", label: "今日" },
  { id: "points", label: "积分" },
]

/** 本地今天 0 点(epoch 秒):「今日」按看榜的人自己的时区算。 */
function startOfToday(): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return Math.floor(d.getTime() / 1000)
}

/**
 * 排行榜。数据来自 POST /leaderboard/stats(总榜 / 今日,按净赢排)与 POST /leaderboard(积分),
 * 都走加密信封、需已登录。名次、VPIP、谁赢谁多少都是后端算好的,这一页只展示(服务器是唯一真相)。
 */
export default function LeaderboardPage() {
  const router = useRouter()
  // 昵称优先从 room store 拿(刚从牌桌回来时现成的),没有再走一次 /user/me —— 同 /history 的做法。
  const roomState = useRoom()
  const [me, setMe] = useState<string | null>(null)

  const [tab, setTab] = useState<Tab>("total")
  const [stats, setStats] = useState<StatsEntry[]>([])
  const [points, setPoints] = useState<LeaderboardEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getSession()) router.replace("/")
  }, [router])

  useEffect(() => {
    if (roomState.me) {
      setMe(roomState.me)
      return
    }
    let alive = true
    fetchProfile()
      .then((p) => alive && setMe(p.nickname))
      .catch(() => {
        /* 拿不到就只是不高亮 */
      })
    return () => {
      alive = false
    }
  }, [roomState.me])

  useEffect(() => {
    if (!getSession()) return
    let alive = true
    setLoading(true)
    setError(null)
    const load =
      tab === "points"
        ? fetchLeaderboard(LEADERBOARD_PAGE_SIZE).then((b) => alive && setPoints(b))
        : fetchLeaderboardStats({ since: tab === "today" ? startOfToday() : undefined, limit: LEADERBOARD_PAGE_SIZE }).then(
            (s) => alive && setStats(s),
          )
    load
      .catch(() => alive && setError("排行榜加载失败,稍后再试。"))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [tab])

  const mine = tab !== "points" ? stats.find((s) => s.nickname === me) : undefined
  const empty = tab === "points" ? points.length === 0 : stats.length === 0

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none absolute inset-0 opacity-10">
        <div className="float absolute -top-4 left-10 text-8xl md:text-9xl">♠</div>
        <div className="float absolute right-10 top-20 text-7xl md:text-8xl" style={{ animationDelay: "0.5s" }}>
          ♥
        </div>
        <div className="float absolute bottom-24 left-6 text-7xl md:text-8xl" style={{ animationDelay: "1s" }}>
          ♣
        </div>
        <div className="float absolute -bottom-4 right-6 text-8xl md:text-9xl" style={{ animationDelay: "1.5s" }}>
          ♦
        </div>
      </div>

      <main className="relative z-10 mx-auto flex w-full max-w-5xl flex-col gap-4 p-4 sm:p-6">
        <AppNav />
        <Card className="flex flex-col gap-3 border-2 border-primary/30 bg-card/95 p-4 shadow-xl sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Leaderboard</p>
            <p className="text-lg font-semibold text-primary">完整排行</p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {tab === "points" ? (
                // 口径必须写,而且要和大厅摘要一字不差(见 service/docs/rest.md「排的是结算后的全局积分」)
                <>
                  榜上是结算后的全局积分,<span className="text-primary">不含桌上筹码</span>。
                </>
              ) : (
                <>
                  按{tab === "today" ? "今天" : "全部牌局"}的<span className="text-primary">净赢</span>排名 ·
                  VPIP = 翻牌前主动入池的比例
                </>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex overflow-hidden rounded-md border border-primary/40" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}
                  className={cn(
                    "px-3 py-1.5 text-xs transition-colors",
                    tab === t.id ? "bg-primary font-semibold text-primary-foreground" : "text-muted-foreground hover:bg-primary/10",
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </Card>

        {error && (
          <Card role="alert" className="border-2 border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive-foreground">
            {error}
          </Card>
        )}

        {/* 我的这段时间:净赢、手数、VPIP、赢谁最多 / 输谁最多 */}
        {!loading && mine && <MyStats s={mine} />}

        {loading ? (
          <Card className="border-2 border-primary/20 bg-card/95 p-10 text-center text-sm text-muted-foreground shadow-xl">
            正在加载排行…
          </Card>
        ) : empty ? (
          <Card className="flex flex-col items-center gap-2 border-2 border-primary/20 bg-card/95 p-10 text-center shadow-xl">
            <span className="text-3xl opacity-40">♠ ♥ ♣ ♦</span>
            <p className="text-sm font-semibold text-card-foreground">
              {tab === "today" ? "今天还没人打牌" : "还没有可排名的玩家"}
            </p>
            <p className="text-xs text-muted-foreground">打完牌结算之后就会出现在这里。</p>
          </Card>
        ) : tab === "points" ? (
          <Card className="border-2 border-primary/20 bg-card/95 p-2 shadow-xl">
            <ul data-testid="leaderboard-list" className="flex flex-col">
              {points.map((e) => (
                <li
                  key={e.nickname}
                  data-testid="leaderboard-row"
                  className={cn(
                    "flex items-center gap-3 rounded px-3 py-2 text-sm",
                    e.nickname === me ? "bg-primary/15 font-semibold text-primary" : "text-card-foreground",
                  )}
                >
                  <RankBadge rank={e.rank} />
                  <Player name={e.nickname} isMe={e.nickname === me} />
                  <span className="num shrink-0 tabular-nums">{e.points.toLocaleString()}</span>
                </li>
              ))}
            </ul>
            {points.length === LEADERBOARD_PAGE_SIZE && (
              <p className="px-3 py-2 text-[11px] text-muted-foreground">只显示前 {LEADERBOARD_PAGE_SIZE} 名。</p>
            )}
          </Card>
        ) : (
          <StatsTable rows={stats} me={me} />
        )}
      </main>
    </div>
  )
}

const GRID =
  "grid grid-cols-[44px_minmax(0,1fr)_76px_52px] items-center gap-x-3 md:grid-cols-[44px_minmax(0,1.3fr)_84px_56px_60px_60px_64px_minmax(0,1fr)_minmax(0,1fr)]"

function StatsTable({ rows, me }: { rows: StatsEntry[]; me: string | null }) {
  return (
    <Card className="border-2 border-primary/20 bg-card/95 p-2 shadow-xl">
      <div className={cn(GRID, "px-3 pb-2 pt-1 text-[10px] uppercase tracking-wider text-muted-foreground")}>
        <span>#</span>
        <span>玩家</span>
        <span className="text-right">净赢</span>
        <span className="text-right">手数</span>
        <span className="hidden text-right md:block">胜率</span>
        <span className="hidden text-right md:block">VPIP</span>
        <span className="hidden text-right md:block">bb/100</span>
        <span className="hidden md:block">赢得最多</span>
        <span className="hidden md:block">输得最多</span>
      </div>
      <ul data-testid="leaderboard-list" className="flex flex-col gap-0.5">
        {rows.map((e) => (
          <li
            key={e.nickname}
            data-testid="leaderboard-row"
            className={cn(
              GRID,
              "rounded-lg px-3 py-2 text-sm",
              e.nickname === me ? "bg-primary/15 ring-1 ring-primary/40" : "hover:bg-white/[0.03]",
            )}
          >
            <RankBadge rank={e.rank} />
            <Player name={e.nickname} isMe={e.nickname === me} sub={`${e.points.toLocaleString()} 积分`} />
            <span className={cn("num text-right font-semibold tabular-nums", netClass(e.net))}>{formatNet(e.net)}</span>
            <span className="num text-right tabular-nums text-card-foreground">{e.hands}</span>
            <span className="num hidden text-right tabular-nums text-card-foreground md:block">{pct(e.win_rate)}</span>
            <span className="num hidden text-right tabular-nums text-card-foreground md:block">{e.vpip === null ? "—" : pct(e.vpip)}</span>
            <span className={cn("num hidden text-right tabular-nums md:block", netClass(e.bb_per_100 ?? 0))}>
              {e.bb_per_100 === null ? "—" : formatBb(e.bb_per_100)}
            </span>
            <span className="hidden min-w-0 md:block">
              <RivalChip r={e.most_won_from} win />
            </span>
            <span className="hidden min-w-0 md:block">
              <RivalChip r={e.most_lost_to} />
            </span>
          </li>
        ))}
      </ul>
      {rows.length === LEADERBOARD_PAGE_SIZE && (
        <p className="px-3 py-2 text-[11px] text-muted-foreground">只显示前 {LEADERBOARD_PAGE_SIZE} 名。</p>
      )}
    </Card>
  )
}

function MyStats({ s }: { s: StatsEntry }) {
  return (
    <Card data-testid="my-stats" className="border-2 border-primary/40 bg-card/95 p-4 shadow-xl">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-3">
          <Avatar name={s.nickname} size={44} className="h-11 w-11 rounded-full ring-2 ring-primary/60" />
          <div>
            <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">我的战绩 · 第 {s.rank} 名</p>
            <p className={cn("num text-2xl font-bold", netClass(s.net))}>{formatNet(s.net)}</p>
          </div>
        </div>
        <Stat label="手数" value={String(s.hands)} />
        <Stat label="胜率" value={pct(s.win_rate)} />
        <Stat label="VPIP" value={s.vpip === null ? "—" : pct(s.vpip)} />
        <Stat label="bb/100" value={s.bb_per_100 === null ? "—" : formatBb(s.bb_per_100)} />
        <div className="flex min-w-0 flex-col gap-1.5">
          <Labeled label="赢得最多">
            <RivalChip r={s.most_won_from} win />
          </Labeled>
          <Labeled label="输得最多">
            <RivalChip r={s.most_lost_to} />
          </Labeled>
        </div>
      </div>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{label}</p>
      <p className="num text-lg font-semibold text-card-foreground">{value}</p>
    </div>
  )
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[10px] text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}

function RankBadge({ rank }: { rank: number }) {
  const medal = rank === 1 ? "bg-gradient-to-b from-amber-300 to-amber-600 text-black" : rank === 2 ? "bg-gradient-to-b from-slate-200 to-slate-400 text-black" : rank === 3 ? "bg-gradient-to-b from-orange-300 to-orange-700 text-black" : ""
  return (
    <span
      className={cn(
        "num flex h-7 w-9 items-center justify-center rounded-md text-xs font-bold tabular-nums",
        medal || "text-muted-foreground",
      )}
    >
      #{rank}
    </span>
  )
}

function Player({ name, isMe, sub }: { name: string; isMe: boolean; sub?: string }) {
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2.5">
      <Avatar name={name} size={32} className="h-8 w-8 shrink-0 rounded-full ring-1 ring-primary/40" />
      <span className="min-w-0">
        <span className={cn("block truncate font-semibold", isMe ? "text-primary" : "text-card-foreground")}>
          {name}
          {isMe && <span className="ml-2 text-[10px] uppercase tracking-wider text-primary/80">你</span>}
        </span>
        {sub && <span className="block truncate text-[10px] text-muted-foreground">{sub}</span>}
      </span>
    </span>
  )
}

function RivalChip({ r, win = false }: { r: Rival | null; win?: boolean }) {
  if (!r) return <span className="text-xs text-muted-foreground">—</span>
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full bg-white/[0.04] py-0.5 pl-0.5 pr-2 ring-1 ring-white/10">
      <Avatar name={r.nickname} size={20} className="h-5 w-5 shrink-0 rounded-full" />
      <span className="truncate text-xs text-card-foreground">{r.nickname}</span>
      <span className={cn("num shrink-0 text-xs font-semibold", win ? "text-emerald-400" : "text-destructive")}>
        {win ? "+" : "−"}
        {r.amount.toLocaleString()}
      </span>
    </span>
  )
}

function netClass(net: number): string {
  if (net === 0) return "text-muted-foreground"
  return net > 0 ? "text-emerald-400" : "text-destructive"
}

function formatNet(net: number): string {
  return (net > 0 ? "+" : "") + net.toLocaleString()
}

/** bb/100:小于 100 留一位小数,大了取整(手数少时这个数会很夸张,小数没意义)。 */
function formatBb(x: number): string {
  const v = Math.abs(x) >= 100 ? Math.round(x) : Math.round(x * 10) / 10
  return formatNet(v)
}

function pct(x: number): string {
  return `${Math.round(x * 100)}%`
}
