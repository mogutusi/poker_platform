"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { fetchLeaderboard, fetchProfile, type LeaderboardEntry } from "@/transport/rest"
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

/**
 * 完整排行榜。数据来自 POST /leaderboard(走加密信封,需已登录 —— 0094 收编)。
 *
 * 这一页只展示后端给的名次,不自己排序、不自己算积分(见 docs/architecture.md:服务器是唯一真相)。
 */
export default function LeaderboardPage() {
  const router = useRouter()
  // 昵称优先从 room store 拿(刚从牌桌回来时现成的),没有再走一次 /user/me —— 同 /history 的做法。
  const roomState = useRoom()
  const [me, setMe] = useState<string | null>(null)

  const [entries, setEntries] = useState<LeaderboardEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getSession()) {
      router.replace("/")
      return
    }
    let alive = true

    void (async () => {
      // 两个请求分开发:排行榜是本页的正题,/user/me 只为高亮自己,它失败不该让整页空着。
      try {
        const board = await fetchLeaderboard(LEADERBOARD_PAGE_SIZE)
        if (alive) setEntries(board)
      } catch {
        if (alive) setError("排行榜加载失败,稍后再试。")
      } finally {
        if (alive) setLoading(false)
      }

      if (roomState.me) {
        if (alive) setMe(roomState.me)
        return
      }
      try {
        const profile = await fetchProfile()
        if (alive) setMe(profile.nickname)
      } catch {
        // 拿不到就只是不高亮,不报错——名次本身是完整的
      }
    })()

    return () => {
      alive = false
    }
  }, [router, roomState.me])

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-4 p-4 sm:p-6">
      <Card className="flex flex-col gap-3 border-2 border-primary/30 bg-card/95 p-4 shadow-xl sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Leaderboard</p>
          <p className="text-lg font-semibold text-primary">完整排行</p>
          {/*
            口径必须写,而且要和大厅摘要一字不差:后端排的是**结算后的全局积分**,买进牌桌的筹码不在里面
            (见 service/docs/rest.md「坑 · 排的是结算后的全局积分」)。把积分全买上桌的人排名会掉得很难看,
            不写这句像 bug。
          */}
          <p className="mt-1 text-[11px] text-muted-foreground">
            榜上是结算后的全局积分,<span className="text-primary">不含桌上筹码</span>。
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
          onClick={() => router.push("/lobby")}
        >
          返回大厅
        </Button>
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
          正在加载排行…
        </Card>
      ) : entries.length === 0 ? (
        <Card className="flex flex-col items-center gap-2 border-2 border-primary/20 bg-card/95 p-10 text-center shadow-xl">
          <span className="text-3xl opacity-40">♠ ♥ ♣ ♦</span>
          <p className="text-sm font-semibold text-card-foreground">还没有可排名的玩家</p>
          <p className="text-xs text-muted-foreground">打完牌结算之后就会出现在这里。</p>
        </Card>
      ) : (
        <Card className="border-2 border-primary/20 bg-card/95 p-2 shadow-xl">
          <ul data-testid="leaderboard-list" className="flex flex-col">
            {entries.map((e) => (
              <Row key={e.nickname} entry={e} isMe={e.nickname === me} />
            ))}
          </ul>
          {entries.length === LEADERBOARD_PAGE_SIZE && (
            // 满页时说一句,免得用户以为榜就这么长。后端上限就是这个数,没有翻页。
            <p className="px-3 py-2 text-[11px] text-muted-foreground">只显示前 {LEADERBOARD_PAGE_SIZE} 名。</p>
          )}
        </Card>
      )}
    </main>
  )
}

function Row({ entry, isMe }: { entry: LeaderboardEntry; isMe: boolean }) {
  return (
    <li
      data-testid="leaderboard-row"
      className={`flex items-center gap-3 rounded px-3 py-2 text-sm ${
        isMe ? "bg-primary/15 font-semibold text-primary" : "text-card-foreground"
      }`}
    >
      {/* 名次由后端给(同分按昵称升序定序,见 service/docs/rest.md),前端不重排 */}
      <span className="w-10 shrink-0 tabular-nums text-xs text-muted-foreground">#{entry.rank}</span>
      <span className="min-w-0 flex-1 truncate">
        {entry.nickname}
        {isMe && <span className="ml-2 text-[10px] uppercase tracking-wider text-primary/80">你</span>}
      </span>
      <span className="shrink-0 tabular-nums">{entry.points.toLocaleString()}</span>
    </li>
  )
}
