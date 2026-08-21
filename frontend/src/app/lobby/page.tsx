"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import type { ErrorCode } from "@/types/wire.gen"
import {
  fetchHands,
  fetchLeaderboard,
  fetchProfile,
  fetchRooms,
  type HandRecord,
  type LeaderboardEntry as ApiLeaderboardEntry,
  type RoomMeta,
} from "@/transport/rest"
import { getSession, endSession } from "@/transport/session"
import { connectLobby, endLocalState } from "@/store/actions"
import { setMe } from "@/store/room"
import { useRoom } from "@/store/useRoom"
import TableSeat from "@/components/TableSeat"
import DmDrawer from "@/components/DmDrawer"
import ConnectionBanner from "@/components/ConnectionBanner"

interface LeaderboardEntry {
  name: string
  points: number
}

interface SeatPlayer {
  id: string
  name: string
  avatar?: string
  points?: number
}

interface Seat {
  number: number
  player?: SeatPlayer
  isButton?: boolean
}

/** 大厅每个模块只给「摘要 + 入口」,详情留给详情页,所以这几个数字都刻意取得很小。 */
const LEADERBOARD_PREVIEW = 5
const HANDS_PREVIEW = 3

const ROOM_STATUS_TEXT: Record<RoomMeta["status"], string> = {
  pending_start: "等待开局",
  hand_started: "牌局进行中",
}

/**
 * 大厅里能收到的服务器拒绝只有私聊那几种(见 service/docs/messaging.md 的防护序)。
 * 后端只回机器码,文案一律由前端映射;没列的 code 原样显示,不装作没发生。
 */
const DM_ERROR_TEXT: Partial<Record<ErrorCode, string>> = {
  INVALID_MESSAGE: "消息为空,或对方昵称不存在。",
  MESSAGE_TOO_LONG: "这条太长了,发不出去。",
  RATE_LIMITED: "发得太快了,缓一缓再发。",
  CANNOT_DM_SELF: "不能给自己发私信。",
}

/**
 * 时间只显示「月-日 时:分」,且手动拼而不用 toLocaleString:
 * 后者的输出随浏览器时区/语言变,SSR 与首屏 hydrate 对不上会报 mismatch。
 */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function LobbyPage() {
  const router = useRouter()
  // 大厅只用得上 room store 里的 lastError:私聊被服务器拒(超长/限速)只回一条 error,
  // 大厅没有别处显示它,不接就是静默失败。
  const roomState = useRoom()
  const [playerName, setPlayerName] = useState<string>("")
  const [points, setPoints] = useState<number | null>(null)
  const [isJoining, setIsJoining] = useState(false)
  const [selectedSeat, setSelectedSeat] = useState<number | null>(null)

  // 排行榜来自 GET /leaderboard(公开读,明文)。它排的是结算后的全局积分,不含桌上筹码。
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [rooms, setRooms] = useState<RoomMeta[]>([])
  const [hands, setHands] = useState<HandRecord[]>([])
  const [roomName, setRoomName] = useState("dev")
  const [loadError, setLoadError] = useState<string | null>(null)
  /**
   * 桌子只画一张(用户的话:大多数时间就一个房间),但不写死是哪一张:
   * 默认第一间,多间时可以切,别的房间不能因此看不见。
   */
  const [roomIndex, setRoomIndex] = useState(0)

  // 房间列表可能在切换过程中被刷新变短,所以每次读都夹一下,不靠 setState 去纠正。
  const activeIndex = rooms.length === 0 ? 0 : Math.min(roomIndex, rooms.length - 1)
  const room = rooms[activeIndex] ?? null

  /**
   * 大厅只能知道「占了几个座」(RoomMeta.seated),知道不了「谁坐哪」——逐座位的详情要
   * join_room 之后由 StateSnapshot 带来(见 docs/state.md)。所以这里渲染的是匿名占位,
   * 不编造玩家名。
   */
  const seats = useMemo<Seat[]>(() => {
    const total = room?.max_seats ?? 9
    const taken = room?.seated ?? 0
    return Array.from({ length: total }, (_, i) => ({
      number: i + 1,
      player: i < taken ? { id: `seat-${i + 1}`, name: "已入座" } : undefined,
    }))
  }, [room])

  const maxPoints = useMemo(
    () => leaderboard.reduce((max, p) => Math.max(max, p.points), 0),
    [leaderboard]
  )

  useEffect(() => {
    // 会话只活在内存里(见 docs/transport.md §六),刷新页面就没了,回登录页重登。
    const session = getSession()
    if (!session) {
      router.replace("/")
      return
    }

    let cancelled = false

    // 大厅也要挂着 ws:私聊跨房间、不需要在房里,而离线期的补收只在(重)连时发生一次。
    // 不在离开大厅时断开——去牌桌那一跳要接着用这条连接(见 transport/ws.ts 的复用),
    // 真正的断开只发生在离开牌桌和登出。
    connectLobby(() => router.replace("/"))

    // 公开读(房间/排行榜)和需身份的 /user/me 分开发:后者失败只是头像卡少两个字段,
    // 不该把整个大厅打成「加载失败」。
    Promise.all([fetchRooms(), fetchLeaderboard(LEADERBOARD_PREVIEW)])
      .then(([roomList, board]) => {
        if (cancelled) return
        setRooms(roomList)
        setLeaderboard(board.map((e: ApiLeaderboardEntry) => ({ name: e.nickname, points: e.points })))
      })
      .catch(() => {
        if (!cancelled) setLoadError("读取大厅数据失败,请确认后端已启动。")
      })

    fetchProfile()
      .then((profile) => {
        if (cancelled) return
        setPlayerName(profile.nickname)
        setPoints(profile.points)
        // 私聊要判「这条是不是我自己发的」,牌桌要判「哪个座位是我的」,都读这一个 me。
        setMe(profile.nickname)
        // 手牌历史按昵称过滤,所以必须等昵称回来才能查,不能和上面并发。
        return fetchHands({ user: profile.nickname, limit: HANDS_PREVIEW }).then((list) => {
          if (!cancelled) setHands(list)
        })
      })
      .catch(() => {
        /* 摘要缺失比整页报错好:这里静默,卡片各自显示占位文案。 */
      })

    return () => {
      cancelled = true
    }
  }, [router])

  /** 进房走 ws 的 join_room;房不存在后端会动态建房。选座在牌桌页做(见 docs/state.md)。 */
  const handleEnterRoom = (roomId: string) => {
    router.push(`/game?room=${encodeURIComponent(roomId)}`)
  }

  const handleSeatClick = (seatNumber: number) => {
    if (!room) return
    setIsJoining(true)
    setSelectedSeat(seatNumber)
    handleEnterRoom(room.id)
  }

  const handleLogout = () => {
    // 断连接之外还要清私聊:它不随离开房间清,只随会话结束清,
    // 否则换个账号登进来会看到上一个人的私信。
    endLocalState()
    endSession()
    router.replace("/")
  }

  const shiftRoom = (delta: number) => {
    if (rooms.length < 2) return
    // 绕圈:房间少的时候来回点箭头比按到头卡住顺手。
    setRoomIndex((activeIndex + delta + rooms.length) % rooms.length)
  }

  return (
    <div className="min-h-screen relative overflow-hidden bg-background text-foreground p-4 md:p-8">
      {/* Background suits to match login page */}
      <div className="pointer-events-none absolute inset-0 opacity-10">
        <div className="absolute -top-4 left-10 text-8xl md:text-9xl float">♠</div>
        <div
          className="absolute top-20 right-10 text-7xl md:text-8xl float"
          style={{ animationDelay: "0.5s" }}
        >
          ♥
        </div>
        <div
          className="absolute bottom-24 left-6 text-7xl md:text-8xl float"
          style={{ animationDelay: "1s" }}
        >
          ♣
        </div>
        <div
          className="absolute -bottom-4 right-6 text-8xl md:text-9xl float"
          style={{ animationDelay: "1.5s" }}
        >
          ♦
        </div>
      </div>

      <div className="relative z-10 mx-auto flex max-w-6xl flex-col gap-6 md:flex-row">
        {/* Left column: user info + table */}
        <div className="flex flex-1 flex-col gap-4">
          {/*
            头像卡整块可点 → /settings。账号设置只留这一个入口,不再加导航项;
            用 button 而不是 div+onClick,键盘和读屏才能到达。
          */}
          <Card className="bg-card/95 border-primary/30/50 flex items-center gap-4 border-2 p-4 shadow-xl">
            <button
              type="button"
              data-testid="profile-card"
              onClick={() => router.push("/settings")}
              aria-label="账号设置"
              className="flex flex-1 items-center gap-4 rounded-lg p-1 text-left transition-colors hover:bg-primary/5"
            >
              <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-full border-2 border-primary/70 bg-accent/40 glow-pulse">
                <div className="flex h-full w-full items-center justify-center text-2xl">
                  {playerName.slice(0, 1).toUpperCase()}
                </div>
              </div>
              <div className="flex flex-1 flex-col">
                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                  Logged in as
                </p>
                <p className="text-lg font-semibold text-primary">{playerName || "…"}</p>
                <div className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
                  <span className="text-xs uppercase tracking-[0.2em]">POINTS</span>
                  <span className="text-base font-semibold text-primary">
                    {points === null ? "—" : points.toLocaleString()}
                  </span>
                  <span className="text-xs text-primary">设置 ›</span>
                </div>
              </div>
            </button>
            <Button
              variant="outline"
              size="sm"
              className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
              onClick={handleLogout}
            >
              退出
            </Button>
          </Card>

          {loadError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {loadError}
            </div>
          )}

          {/* 大厅唯一会来的服务器拒绝就是私聊那几种(超长/限速)。文案由前端按 code 映射,
              后端只回机器码;还没映射的直接把 code 显出来,总比什么都不显示强。 */}
          {roomState.lastError && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              私聊被拒:{DM_ERROR_TEXT[roomState.lastError.code] ?? roomState.lastError.code}
            </div>
          )}

          {/* Poker table area */}
          <Card className="relative flex flex-1 flex-col justify-between gap-6 overflow-hidden bg-card/95 border-2 border-primary/30 p-5 shadow-2xl">
            {/* Table felt */}
            <div className="pointer-events-none absolute inset-0 bg-radial from-accent/40 via-accent/10 to-transparent opacity-60" />

            <div className="relative flex flex-1 flex-col items-center justify-between gap-6">
              <div className="w-full text-left">
                <div className="flex items-center justify-between">
                  <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">
                    Main Table
                  </p>
                  {/* 多房间时的轻量切换:左右箭头 + 序号,不把别的房间藏进二级页面。 */}
                  {rooms.length > 1 && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <button
                        type="button"
                        aria-label="上一个房间"
                        onClick={() => shiftRoom(-1)}
                        className="rounded border border-primary/30 px-2 py-0.5 hover:bg-primary/10"
                      >
                        ‹
                      </button>
                      <span className="tabular-nums">
                        {activeIndex + 1} / {rooms.length}
                      </span>
                      <button
                        type="button"
                        aria-label="下一个房间"
                        onClick={() => shiftRoom(1)}
                        className="rounded border border-primary/30 px-2 py-0.5 hover:bg-primary/10"
                      >
                        ›
                      </button>
                    </div>
                  )}
                </div>
                <div className="mt-1 flex items-center justify-between gap-3">
                  <p className="truncate text-xl font-semibold text-primary">
                    {room ? room.id : "还没有开桌"}
                  </p>
                  <span className="shrink-0 rounded-full bg-secondary/60 px-3 py-1 text-xs text-secondary-foreground">
                    {room ? `${room.seated} / ${room.max_seats} Players` : "空桌"}
                  </span>
                </div>
                {rooms.length > 1 && (
                  <select
                    aria-label="选择房间"
                    value={activeIndex}
                    onChange={(e) => setRoomIndex(Number(e.target.value))}
                    className="mt-2 h-8 w-full rounded border border-primary/30 bg-black/30 px-2 text-xs text-foreground"
                  >
                    {rooms.map((r, i) => (
                      <option key={r.id} value={i}>
                        {r.id} · {r.small_blind}/{r.big_blind} · {r.seated}/{r.max_seats}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {/*
                房间是动态创建的:进一个不存在的房名,后端就地建房(见 service/docs/core.md 房间生命周期)。
                所以大厅必须有这个入口——否则空大厅时用户点哪儿都进不去。
              */}
              <div className="flex w-full items-center gap-2">
                <input
                  aria-label="房间名"
                  value={roomName}
                  onChange={(e) => setRoomName(e.target.value)}
                  placeholder="房间名"
                  className="h-9 flex-1 rounded border border-primary/40 bg-black/30 px-3 text-sm"
                />
                <Button
                  // 文案随「有没有房」变(进入 / 开一桌),所以给测试留一个稳定的定位钩子:
                  // 按文案定位会在空大厅时失效,而文案本来就该随状态变。
                  data-testid="enter-room"
                  onClick={() => roomName.trim() && handleEnterRoom(roomName.trim())}
                  className="bg-primary hover:bg-primary/90 text-primary-foreground font-semibold"
                >
                  {room ? "进入房间" : "开一桌"}
                </Button>
              </div>


              {/* Oval poker table with seats */}
              <div className="relative flex w-full flex-1 items-center justify-center min-h-[500px] py-12">
                <div className="relative h-full w-full max-w-4xl aspect-[2/1] flex items-center justify-center">
                  {/* Outer table rail (wood/leather border) */}
                  <div
                    className="absolute inset-0"
                    style={{
                      borderRadius: '50%',
                      clipPath: 'ellipse(100% 65% at 50% 50%)',
                      background: 'linear-gradient(135deg, #2d1810 0%, #1a0f08 50%, #2d1810 100%)',
                      boxShadow: '0 8px 32px rgba(0,0,0,0.6), inset 0 2px 8px rgba(255,255,255,0.1)',
                      border: '6px solid',
                      borderColor: '#1a0f08',
                    }}
                  />

                  {/* Inner rail (cushion) */}
                  <div
                    className="absolute inset-2"
                    style={{
                      borderRadius: '50%',
                      clipPath: 'ellipse(100% 65% at 50% 50%)',
                      background: 'linear-gradient(135deg, #3d2818 0%, #2d1810 50%, #3d2818 100%)',
                      boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.5)',
                    }}
                  />

                  {/* Texas Hold'em felt surface */}
                  <div
                    className="absolute inset-4"
                    style={{
                      borderRadius: '50%',
                      clipPath: 'ellipse(100% 65% at 50% 50%)',
                      background: 'linear-gradient(135deg, #0d5d2e 0%, #0a4d26 25%, #0d5d2e 50%, #0a4d26 75%, #0d5d2e 100%)',
                      backgroundImage: `
                        radial-gradient(ellipse at 30% 30%, rgba(34,139,34,0.4) 0%, transparent 50%),
                        radial-gradient(ellipse at 70% 70%, rgba(0,100,0,0.3) 0%, transparent 50%),
                        repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)
                      `,
                      boxShadow: 'inset 0 4px 20px rgba(0,0,0,0.4), inset 0 -2px 10px rgba(0,0,0,0.2), 0 0 40px rgba(0,100,0,0.3)',
                    }}
                  />

                  {/* Betting line (inner oval) */}
                  <div
                    className="absolute inset-8"
                    style={{
                      borderRadius: '50%',
                      clipPath: 'ellipse(100% 65% at 50% 50%)',
                      border: '1px dashed',
                      borderColor: 'rgba(212,175,55,0.3)',
                    }}
                  />

                  {/* Seat positions arranged evenly around oval table */}
                  <div className="absolute inset-0 flex items-center justify-center">
                    {seats.map((seat, index) => {
                      // Custom angle adjustments for even visual spacing around oval
                      // Balancing spacing: reduce gaps between 9-2 and 5-7, maintain spacing for 2-4 and 7-8
                      // Seat mapping: 1=top, 2-4=right side, 5=bottom, 6-8=left side, 9=top-left
                      const angleAdjustments = [
                        0,   // Seat 1
                        -7,  // Seat 2
                        4,   // Seat 3
                        14,  // Seat 4
                        7,   // Seat 5
                        -3,  // Seat 6
                        -12, // Seat 7
                        4,   // Seat 8
                        8,   // Seat 9
                      ]

                      const baseAngle = index * 40 - 90 // Base angle starting from top
                      const adjustment = angleAdjustments[index] || 0
                      const angleDeg = baseAngle + adjustment
                      const angle = (angleDeg * Math.PI) / 180 // Convert to radians

                      // Oval table dimensions: width 100%, height 65%
                      // Position seats at consistent distance from table edge
                      const normalizedRadius = 0.82 // 82% of table radius

                      // Calculate position on ellipse
                      // Oval: x = a*cos(θ), y = b*sin(θ) where a=50% (half width), b=32.5% (half height of 65%)
                      const radiusX = 50 * normalizedRadius // Horizontal radius: 41%
                      const radiusY = 32.5 * normalizedRadius // Vertical radius: ~26.65%

                      // Calculate position on ellipse - seat center position
                      const x = 50 + radiusX * Math.cos(angle) // Center at 50%
                      const y = 50 + radiusY * Math.sin(angle) // Center at 50%

                      // Round to 2 decimal places to prevent hydration mismatches
                      const roundedX = Math.round(x * 100) / 100
                      const roundedY = Math.round(y * 100) / 100

                      return (
                        <div
                          key={seat.number}
                          className="absolute"
                          style={{
                            left: `${roundedX}%`,
                            top: `${roundedY}%`,
                            transform: "translate(-50%, -50%)",
                            zIndex: 10,
                          }}
                        >
                          {/*
                            没有房间时桌子照常显示(空桌),但座位不给点:此刻没有房可进,
                            点了只会静默无反应,不如让「点击入座」的提示也一并消失。
                          */}
                          <TableSeat
                            seatNumber={seat.number}
                            player={seat.player}
                            isButton={seat.isButton}
                            onClick={room ? () => handleSeatClick(seat.number) : undefined}
                            className={
                              selectedSeat === seat.number && isJoining
                                ? "opacity-50"
                                : ""
                            }
                          />
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>

              <div className="flex w-full items-center justify-between gap-4">
                {/* 桌况一律取自 RoomMeta,不写死也不编造(旧版这里是硬编码的盲注和假倒计时)。 */}
                <div className="text-xs text-muted-foreground">
                  {room ? (
                    <>
                      <p>
                        盲注 {room.small_blind} / {room.big_blind} · 买入 {room.buy_in.toLocaleString()}
                      </p>
                      <p>
                        {ROOM_STATUS_TEXT[room.status]} · 观战 {room.watching}
                      </p>
                    </>
                  ) : (
                    <>
                      <p>大厅里还没有房间。</p>
                      <p>填个房名点「开一桌」,后端会就地建房。</p>
                    </>
                  )}
                </div>
                {isJoining && (
                  <div className="text-sm text-primary font-semibold">
                    正在加入座位 {selectedSeat}...
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>

        {/* Right column: leaderboard + recent hands(都是摘要,详情在各自的详情页) */}
        <div className="mt-2 flex w-full flex-col gap-4 md:mt-0 md:w-80 lg:w-96">
          <Card className="flex flex-col bg-card/95 border-2 border-primary/30 p-4 shadow-xl">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">
                  Leaderboard
                </p>
                <p className="text-lg font-semibold text-primary">今日牌桌英雄榜</p>
              </div>
              <span className="rounded-full bg-accent/40 px-3 py-1 text-xs text-accent-foreground">
                Top {leaderboard.length}
              </span>
            </div>

            <div className="mt-2 flex-1 space-y-3">
              {leaderboard.length === 0 && (
                <p className="text-xs text-muted-foreground">还没有人上榜。</p>
              )}
              {leaderboard.map((entry, index) => {
                const ratio = maxPoints ? entry.points / maxPoints : 0
                return (
                  <div
                    key={entry.name}
                    data-testid="leaderboard-entry"
                    className="relative overflow-hidden rounded-lg bg-secondary/60 p-3 text-xs shadow-inner"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/80 text-xs font-bold text-primary-foreground">
                          {index + 1}
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-card-foreground">
                            {entry.name}
                          </p>
                          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                            {entry.points.toLocaleString()} pts
                          </p>
                        </div>
                      </div>
                      <span className="text-[11px] text-primary">
                        {Math.round(ratio * 100)}%
                      </span>
                    </div>
                    <div className="mt-2 h-1.5 w-full rounded-full bg-card/70">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-primary via-accent to-destructive transition-all duration-500"
                        style={{ width: `${Math.max(10, ratio * 100)}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>

            {/*
              必须标注口径:后端排的是**结算后的全局积分**,买进牌桌的筹码不在里面
              (见 service/docs/rest.md「坑 · 排的是结算后的全局积分」)。一个人把积分
              全买上桌之后排名会掉得很难看,不写这句话像 bug。
            */}
            <p className="mt-4 text-[11px] text-muted-foreground">
              榜上是结算后的全局积分,<span className="text-primary">不含桌上筹码</span>。
            </p>
            {/* 排行榜详情页本轮不做,所以这里是禁用占位,不指向一个不存在的路由。 */}
            <Button
              variant="outline"
              size="sm"
              disabled
              title="完整排行页尚未实现"
              className="mt-2 w-full border-primary/30 bg-card/60 text-xs"
            >
              查看完整排行(待建)
            </Button>
          </Card>

          <Card className="flex flex-col bg-card/95 border-2 border-primary/30 p-4 shadow-xl">
            <div className="mb-3">
              <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">
                Recent Hands
              </p>
              <p className="text-lg font-semibold text-primary">我的最近手牌</p>
            </div>

            <div className="space-y-2">
              {hands.length === 0 && (
                <p className="text-xs text-muted-foreground">还没有打完的手牌。</p>
              )}
              {hands.map((h) => {
                // 只显示「自己」那一行的盈亏:participants 里可能有好几个人,别人的与我无关。
                const mine = h.participants.find((p) => p.nickname === playerName)
                const net = mine?.net ?? 0
                return (
                  <div
                    key={h.id}
                    className="flex items-center justify-between rounded-lg bg-secondary/60 px-3 py-2 text-xs shadow-inner"
                  >
                    <div>
                      <p className="text-card-foreground">{formatTime(h.end_time)}</p>
                      <p className="text-[10px] text-muted-foreground">
                        底池 {h.final_pot.toLocaleString()}
                      </p>
                    </div>
                    <span
                      className={
                        net > 0
                          ? "text-sm font-semibold text-primary"
                          : net < 0
                            ? "text-sm font-semibold text-destructive"
                            : "text-sm font-semibold text-muted-foreground"
                      }
                    >
                      {net > 0 ? "+" : ""}
                      {net.toLocaleString()}
                    </span>
                  </div>
                )
              })}
            </div>

            <Button
              variant="outline"
              size="sm"
              className="mt-3 w-full border-primary/30 bg-card/60 text-xs hover:bg-primary/10"
              data-testid="history-entry"
              onClick={() => router.push("/history")}
            >
              查看全部手牌 ›
            </Button>
          </Card>
        </div>
      </div>

      {/* 私聊浮标。大厅和牌桌挂同一个组件、同一个位置(右下角),换页不用重新找入口。 */}
      <ConnectionBanner />
      <DmDrawer />
    </div>
  )
}
