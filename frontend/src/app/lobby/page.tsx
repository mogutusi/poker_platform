"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import {
  fetchLeaderboard,
  fetchProfile,
  fetchRooms,
  logout,
  type LeaderboardEntry as ApiLeaderboardEntry,
  type RoomMeta,
} from "@/transport/rest"
import { getSession, endSession } from "@/transport/session"
import { connectLobby, endLocalState } from "@/store/actions"
import { setMe } from "@/store/room"
import TableSeat from "@/components/TableSeat"
import Avatar from "@/components/Avatar"
import { seatAngle } from "@/components/tableSeats"
import ConnectionBanner from "@/components/ConnectionBanner"
import AppNav from "@/components/AppNav"

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
/** 房间列表刷新间隔。大厅还没有实时推送(lobby.md:LobbyBroadcast 未做),靠轮询让桌面预览跟上入座变化。 */
const ROOMS_POLL_MS = 4000

const ROOM_STATUS_TEXT: Record<RoomMeta["status"], string> = {
  pending_start: "等待开局",
  hand_started: "牌局进行中",
}

export default function LobbyPage() {
  const router = useRouter()
  const [playerName, setPlayerName] = useState<string>("")
  const [points, setPoints] = useState<number | null>(null)
  const [isJoining, setIsJoining] = useState(false)
  const [selectedSeat, setSelectedSeat] = useState<number | null>(null)

  // 排行榜来自 POST /leaderboard(走加密信封,需已登录 —— 0094 收编)。它排的是结算后的全局积分,不含桌上筹码。
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [rooms, setRooms] = useState<RoomMeta[]>([])
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
   * 桌面预览:房间列表带着已占座位(谁坐哪、桌上多少筹码,都是公开信息),所以这里画的是真实的人。
   * 还没有房间时画一张 9 座的空桌。
   */
  const seats = useMemo<Seat[]>(() => {
    const total = room?.max_seats ?? 9
    return Array.from({ length: total }, (_, i) => {
      const taken = room?.seats?.find((s) => s.seat_position === i)
      return {
        number: i + 1,
        player: taken ? { id: taken.nickname, name: taken.nickname, points: taken.points } : undefined,
      }
    })
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

    // 大厅也挂着 ws:账号在别处登录 / 会话失效时大厅也能及时退回登录页。
    // 不在离开大厅时断开——去牌桌那一跳要接着用这条连接(见 transport/ws.ts 的复用),
    // 真正的断开只发生在离开牌桌和登出。
    connectLobby((reason) => router.replace(`/?reason=${reason}`))

    // 房间/排行榜与 /user/me 分开发:三者都走信封(0094 起没有明文读了),但 /user/me 失败
    // 只是头像卡少两个字段,不该把整个大厅打成「加载失败」。
    Promise.all([fetchRooms(), fetchLeaderboard(LEADERBOARD_PREVIEW)])
      .then(([roomList, board]) => {
        if (cancelled) return
        setRooms(roomList)
        setLeaderboard(board.map((e: ApiLeaderboardEntry) => ({ name: e.nickname, points: e.points })))
      })
      .catch(() => {
        if (!cancelled) setLoadError("读取大厅数据失败,请确认后端已启动。")
      })
    // 之后只轮询房间列表,让桌面预览上的人与筹码跟着变。失败就等下一轮,不覆盖上面的报错。
    const poll = setInterval(() => {
      fetchRooms()
        .then((roomList) => {
          if (!cancelled) setRooms(roomList)
        })
        .catch(() => undefined)
    }, ROOMS_POLL_MS)

    fetchProfile()
      .then((profile) => {
        if (cancelled) return
        setPlayerName(profile.nickname)
        setPoints(profile.points)
        // 牌桌要判「哪个座位是我的、哪句聊天是我说的」,都读这一个 me。
        setMe(profile.nickname)
      })
      .catch(() => {
        /* 摘要缺失比整页报错好:这里静默,卡片各自显示占位文案。 */
      })

    return () => {
      cancelled = true
      clearInterval(poll)
    }
  }, [router])

  /** 进房走 ws 的 join_room;房不存在后端会动态建房。选座在牌桌页做(见 docs/state.md)。 */
  const handleEnterRoom = (roomId: string) => {
    router.push(`/game?room=${encodeURIComponent(roomId)}`)
  }

  /** 点空座 = 进房并直接坐到这个座位(牌桌页读 ?seat=);点有人的座位就只是进房观战。 */
  const handleSeatClick = (seatNumber: number) => {
    if (!room) return
    setIsJoining(true)
    setSelectedSeat(seatNumber)
    const taken = room.seats?.some((s) => s.seat_position === seatNumber - 1)
    router.push(`/game?room=${encodeURIComponent(room.id)}${taken ? "" : `&seat=${seatNumber}`}`)
  }

  const handleLogout = () => {
    // 先告诉服务器吊销这个会话,再清本地。不告诉的话「退出」只是本地假象:服务器上那把
    // session_token 一直有效到 SESSION_TTL 到期,谁拿到都还能收发(0097 / BUG-8)。
    //
    // best-effort:吊销失败(断网、服务器挂了)也照样把人退出去。本地已经清空、ws 也断了,
    // 卡在这里只会让用户退不出去;那把 token 最坏也就活到自然到期,与修之前同级。
    //
    // **顺序是必须的,不是风格**:logout() 同步地读会话、取 seq、封好帧,之后才 await fetch;
    // 所以它必须排在 endSession() 之前。反过来写的话 requireSession() 会抛,被 catch 吞掉,
    // 「退出」就又变回只清本地了——而且一声不响。
    void logout().catch(() => undefined)
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

      <AppNav className="relative z-20 mx-auto mb-5 max-w-6xl" />

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
                {playerName ? (
                  <Avatar name={playerName} size={56} className="h-full w-full" />
                ) : (
                  <div className="h-full w-full" />
                )}
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
                    {seats.map((seat) => {
                      // 方向取自牌桌页的座位点位(components/tableSeats.ts):同一个人在大厅和牌桌上坐在
                      // 桌子的同一侧。再把这个方向投到椭圆上。
                      const theta = seatAngle(seat.number)
                      const normalizedRadius = 0.82 // 82% of table radius
                      const radiusX = 50 * normalizedRadius // Horizontal radius: 41%
                      const radiusY = 32.5 * normalizedRadius // Vertical radius: ~26.65%
                      // 椭圆上方向为 theta 的点:参数 t 满足 tan t = (rx/ry)·tan theta
                      const angle = Math.atan2(radiusX * Math.sin(theta), radiusY * Math.cos(theta))

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

        {/* Right column: leaderboard 摘要(详情在「排行榜」页签;手牌历史在「数据」页签) */}
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
            {/* 详情页已落地(0104):这里从禁用占位换成真入口。大厅只给摘要,全量在 /leaderboard。 */}
            <Button
              variant="outline"
              size="sm"
              data-testid="leaderboard-more"
              className="mt-2 w-full border-primary/30 bg-card/60 text-xs hover:bg-primary/10"
              onClick={() => router.push("/leaderboard")}
            >
              查看完整排行
            </Button>
          </Card>

        </div>
      </div>

      <ConnectionBanner />
    </div>
  )
}
