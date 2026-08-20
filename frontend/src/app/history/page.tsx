"use client"

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { fetchHands, fetchProfile, handRoom, type HandParticipant, type HandRecord } from "@/transport/rest"
import { getSession } from "@/transport/session"
import { useRoom } from "@/store/useRoom"

/** 一页的条数。后端 /hands 的 limit 上限见 service/app/rest/hands.py,20 远在其下。 */
const PAGE_SIZE = 20

type Scope = "mine" | "all"

/**
 * 手牌历史。数据全部来自 GET /hands(公开读、明文),这一页只展示结果,
 * 不复算任何牌局(见 docs/architecture.md 不变量 1)——后端记的就是最终的 net,不是我们算的。
 */
export default function HistoryPage() {
  const router = useRouter()
  // 昵称优先从 room store 拿(刚从牌桌回来时现成的),没有再走一次 /user/me。
  const roomState = useRoom()
  const [me, setMe] = useState<string | null>(null)
  const [meFailed, setMeFailed] = useState(false)

  const [scope, setScope] = useState<Scope>("mine")
  const [hands, setHands] = useState<HandRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 上一页返回不足 PAGE_SIZE 即到底,不再显示「加载更多」。 */
  const [exhausted, setExhausted] = useState(false)

  /**
   * 每次换筛选条件就 +1;异步回来时对不上就丢弃结果。
   * 否则慢的「我的」那一批可能盖掉后点的「全部」。
   */
  const requestId = useRef(0)

  useEffect(() => {
    // 会话只活在内存里(docs/transport.md §六),刷新即失效,回登录页。
    if (!getSession()) {
      router.replace("/")
      return
    }
    if (roomState.me) {
      setMe(roomState.me)
      return
    }
    let cancelled = false
    fetchProfile()
      .then((p) => {
        if (!cancelled) setMe(p.nickname)
      })
      .catch(() => {
        if (!cancelled) setMeFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [router, roomState.me])

  // 看「全部」时过滤器恒为 undefined,这样 me 迟到解析出来不会白白重拉一次全站列表。
  const userFilter = scope === "mine" ? (me ?? undefined) : undefined
  const blocked = scope === "mine" && !me

  // 首屏 / 换筛选:重新从最新一条开始拉。
  useEffect(() => {
    // 看「我的」但还不知道我是谁时先按加载中挂着,别拿 user=undefined 去拉成全站列表。
    if (blocked) {
      if (meFailed) {
        setLoading(false)
        setError("读取账号信息失败,无法筛选自己的手牌。")
      }
      return
    }

    const id = ++requestId.current
    setLoading(true)
    setError(null)
    setHands([])
    setExhausted(false)

    fetchHands({ user: userFilter, limit: PAGE_SIZE })
      .then((page) => {
        if (requestId.current !== id) return
        setHands(page)
        setExhausted(page.length < PAGE_SIZE)
        setLoading(false)
      })
      .catch(() => {
        if (requestId.current !== id) return
        setError("读取手牌历史失败,请确认后端已启动。")
        setLoading(false)
      })
  }, [userFilter, blocked, meFailed])

  /** 翻下一页:游标是列表最后一条(也就是最旧一条)的 id,后端取 id 严格小于它的。 */
  const loadMore = useCallback(() => {
    const last = hands[hands.length - 1]
    if (!last || loadingMore || exhausted) return
    const id = requestId.current
    setLoadingMore(true)
    setError(null)
    fetchHands({ user: userFilter, before: last.id, limit: PAGE_SIZE })
      .then((page) => {
        if (requestId.current !== id) return
        setHands((prev) => [...prev, ...page])
        setExhausted(page.length < PAGE_SIZE)
        setLoadingMore(false)
      })
      .catch(() => {
        if (requestId.current !== id) return
        setError("加载更多失败,请重试。")
        setLoadingMore(false)
      })
  }, [hands, loadingMore, exhausted, userFilter])

  return (
    <div className="min-h-screen relative overflow-hidden bg-background text-foreground p-4 md:p-8">
      {/* 与登录/大厅同一套背景花色,保持视觉连续 */}
      <div className="pointer-events-none absolute inset-0 opacity-10">
        <div className="absolute -top-4 left-10 text-8xl md:text-9xl float">♠</div>
        <div className="absolute top-20 right-10 text-7xl md:text-8xl float" style={{ animationDelay: "0.5s" }}>
          ♥
        </div>
        <div className="absolute bottom-24 left-6 text-7xl md:text-8xl float" style={{ animationDelay: "1s" }}>
          ♣
        </div>
        <div className="absolute -bottom-4 right-6 text-8xl md:text-9xl float" style={{ animationDelay: "1.5s" }}>
          ♦
        </div>
      </div>

      <div className="relative z-10 mx-auto flex max-w-4xl flex-col gap-4">
        <Card className="flex flex-col gap-3 border-2 border-primary/30 bg-card/95 p-4 shadow-xl sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Hand History</p>
            <p className="text-lg font-semibold text-primary">手牌历史</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {scope === "mine" ? `只看 ${me ?? "…"} 参与的牌局` : "全部牌局"} · 新→旧
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* 只有大厅一个出口:这一页没有任何进房/操作入口,防止从历史里误触牌局 */}
            <div className="flex overflow-hidden rounded-md border border-primary/40">
              <ScopeTab active={scope === "mine"} disabled={meFailed} onClick={() => setScope("mine")}>
                我的
              </ScopeTab>
              <ScopeTab active={scope === "all"} onClick={() => setScope("all")}>
                全部
              </ScopeTab>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
              onClick={() => router.push("/lobby")}
            >
              返回大厅
            </Button>
          </div>
        </Card>

        {error && (
          <Card
            role="alert"
            className="border-2 border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive-foreground"
          >
            {error}
          </Card>
        )}

        {loading ? (
          <Card className="border-2 border-primary/20 bg-card/95 p-10 text-center text-sm text-muted-foreground shadow-xl">
            正在加载手牌…
          </Card>
        ) : hands.length === 0 ? (
          <Card className="flex flex-col items-center gap-2 border-2 border-primary/20 bg-card/95 p-10 text-center shadow-xl">
            <span className="text-3xl opacity-40">♠ ♥ ♣ ♦</span>
            <p className="text-sm font-semibold text-card-foreground">
              {scope === "mine" ? "还没有打过牌" : "还没有任何牌局记录"}
            </p>
            <p className="text-xs text-muted-foreground">牌局结算后会自动记在这里。</p>
            <Button
              size="sm"
              className="mt-2 bg-primary font-semibold text-primary-foreground hover:bg-primary/90"
              onClick={() => router.push("/lobby")}
            >
              去大厅找一桌
            </Button>
          </Card>
        ) : (
          <>
            <div className="flex flex-col gap-3">
              {hands.map((h) => (
                <HandRow key={h.id} hand={h} me={me} />
              ))}
            </div>

            <div className="pb-6 text-center">
              {exhausted ? (
                <p className="text-xs text-muted-foreground">— 没有更早的记录了 —</p>
              ) : (
                <Button
                  variant="outline"
                  className="border-primary/40 bg-card/60 hover:bg-primary/10"
                  disabled={loadingMore}
                  onClick={loadMore}
                >
                  {loadingMore ? "加载中…" : "加载更多"}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function ScopeTab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      onClick={onClick}
      className={
        "px-3 py-1.5 text-xs transition-colors disabled:opacity-40 " +
        (active ? "bg-primary text-primary-foreground font-semibold" : "text-muted-foreground hover:bg-primary/10")
      }
    >
      {children}
    </button>
  )
}

function HandRow({ hand, me }: { hand: HandRecord; me: string | null }) {
  const mine = me ? hand.participants.find((p) => p.nickname === me) : undefined

  return (
    <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-lg">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate rounded-full bg-secondary/60 px-2.5 py-0.5 text-xs text-secondary-foreground">
              {handRoom(hand)}
            </span>
            <span className="text-[11px] text-muted-foreground">{formatTime(hand.end_time)}</span>
            <span className="text-[11px] text-muted-foreground">
              {formatDuration(hand.start_time, hand.end_time)}
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            底池 <span className="text-sm font-semibold text-primary">{hand.final_pot.toLocaleString()}</span>
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
            {mine ? "我的盈亏" : "未参与"}
          </p>
          <p className={"text-xl font-bold " + netClass(mine?.net)}>{mine ? formatNet(mine.net) : "—"}</p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 border-t border-border/60 pt-3">
        {hand.participants.map((p) => (
          <PlayerChip key={p.nickname} p={p} isMe={p.nickname === me} />
        ))}
      </div>
    </Card>
  )
}

function PlayerChip({ p, isMe }: { p: HandParticipant; isMe: boolean }) {
  return (
    <span
      className={
        "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] " +
        (isMe ? "bg-primary/15 ring-1 ring-primary/50" : "bg-secondary/40")
      }
    >
      <span className={isMe ? "font-semibold text-primary" : "text-card-foreground"}>{p.nickname}</span>
      <span className={netClass(p.net)}>{formatNet(p.net)}</span>
    </span>
  )
}

function netClass(net: number | undefined): string {
  if (net === undefined || net === 0) return "text-muted-foreground"
  return net > 0 ? "text-emerald-400" : "text-destructive"
}

function formatNet(net: number): string {
  return (net > 0 ? "+" : "") + net.toLocaleString()
}

const pad = (n: number) => String(n).padStart(2, "0")

/** 后端盖的是 ISO 墙钟串;这里手动格式化,不用 toLocaleString,免得服务端/客户端 locale 不一致。 */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (!Number.isFinite(ms) || ms < 0) return ""
  const sec = Math.round(ms / 1000)
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${pad(sec % 60)}s`
}
