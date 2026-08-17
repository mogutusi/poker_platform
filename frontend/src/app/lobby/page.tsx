"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { fetchLeaderboard, fetchRooms, type LeaderboardEntry as ApiLeaderboardEntry, type RoomMeta } from "@/transport/rest"
import { getSession, endSession } from "@/transport/session"
import { disconnect } from "@/transport/ws"
import TableSeat from "@/components/TableSeat"

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

export default function LobbyPage() {
  const router = useRouter()
  const [playerName, setPlayerName] = useState<string>("")
  const [points, setPoints] = useState<number>(1000)
  const [isJoining, setIsJoining] = useState(false)
  const [selectedSeat, setSelectedSeat] = useState<number | null>(null)

  // 排行榜来自 GET /leaderboard(公开读,明文)。它排的是结算后的全局积分,不含桌上筹码。
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [rooms, setRooms] = useState<RoomMeta[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  /**
   * 大厅只能知道「占了几个座」(RoomMeta.seated),知道不了「谁坐哪」——逐座位的详情要
   * join_room 之后由 StateSnapshot 带来(见 docs/state.md)。所以这里渲染的是匿名占位,
   * 不编造玩家名。
   */
  const room = rooms[0] ?? null
  const seats = useMemo<Seat[]>(() => {
    const total = room?.max_seats ?? 9
    const taken = room?.seated ?? 0
    return Array.from({ length: total }, (_, i) => ({
      number: i + 1,
      player: i < taken ? { id: `seat-${i + 1}`, name: "已入座" } : undefined,
    }))
  }, [room])

  const currentPlayers = useMemo(
    () => seats.filter((seat) => seat.player).length,
    [seats]
  )

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
    Promise.all([fetchRooms(), fetchLeaderboard(10)])
      .then(([roomList, board]) => {
        if (cancelled) return
        setRooms(roomList)
        setLeaderboard(board.map((e: ApiLeaderboardEntry) => ({ name: e.nickname, points: e.points })))
      })
      .catch(() => {
        if (!cancelled) setLoadError("读取大厅数据失败,请确认后端已启动。")
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
    disconnect()
    endSession()
    router.replace("/")
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
          {/* User info card */}
          <Card className="bg-card/95 border-primary/30/50 flex items-center gap-4 border-2 p-4 shadow-xl">
            <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-full border-2 border-primary/70 bg-accent/40 glow-pulse">
              <div className="flex h-full w-full items-center justify-center text-2xl">
                {playerName.slice(0, 1).toUpperCase()}
              </div>
            </div>
            <div className="flex flex-1 flex-col">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                    Logged in as
                  </p>
                  <p className="text-lg font-semibold text-primary">{playerName}</p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
                  onClick={handleLogout}
                >
                  退出
                </Button>
              </div>
              <div className="mt-2 flex items-center justify-between text-sm text-muted-foreground">
                <div className="flex items-center gap-2">
                  <span className="text-xs uppercase tracking-[0.2em]">POINTS</span>
                  <span className="text-base font-semibold text-primary">{points.toLocaleString()}</span>
                </div>
                <button className="text-xs text-primary hover:underline">设置</button>
              </div>
            </div>
          </Card>

          {/* Poker table area */}
          <Card className="relative flex flex-1 flex-col justify-between gap-6 overflow-hidden bg-card/95 border-2 border-primary/30 p-5 shadow-2xl">
            {/* Table felt */}
            <div className="pointer-events-none absolute inset-0 bg-radial from-accent/40 via-accent/10 to-transparent opacity-60" />

            <div className="relative flex flex-1 flex-col items-center justify-between gap-6">
              <div className="w-full text-left">
                <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">
                  Main Table
                </p>
                <div className="mt-1 flex items-center justify-between">
                  <p className="text-xl font-semibold text-primary">德州扑克主桌</p>
                  <span className="rounded-full bg-secondary/60 px-3 py-1 text-xs text-secondary-foreground">
                    {currentPlayers} / 9 Players
                  </span>
                </div>
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
                          <TableSeat
                            seatNumber={seat.number}
                            player={seat.player}
                            isButton={seat.isButton}
                            onClick={() => handleSeatClick(seat.number)}
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
                <div className="text-xs text-muted-foreground">
                  <p>当前盲注：25 / 50</p>
                  <p>下一局将在 00:32 后开始</p>
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

        {/* Right column: leaderboard */}
        <div className="mt-2 w-full md:mt-0 md:w-80 lg:w-96">
          <Card className="flex h-full flex-col bg-card/95 border-2 border-primary/30 p-4 shadow-xl">
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
              {leaderboard.map((entry, index) => {
                const ratio = maxPoints ? entry.points / maxPoints : 0
                return (
                  <div
                    key={entry.name}
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

            <div className="mt-4 text-[11px] text-muted-foreground">
              <p>根据当前牌桌积分实时更新，前 3 名将获得特别筹码奖励。</p>
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}


