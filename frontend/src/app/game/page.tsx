"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import PlayerInfoCard from "@/components/PlayerInfoCard"
import PokerCard from "@/components/PokerCard"
import DmDrawer from "@/components/DmDrawer"
import ConnectionBanner from "@/components/ConnectionBanner"
import FreeEntryVote from "@/components/FreeEntryVote"
import RoomConfig from "@/components/RoomConfig"
import HandResult from "@/components/HandResult"
import Image from "next/image"
import gamingTableBg from "@/pics/game-table.jpg"
import { cn } from "@/lib/utils"
import { toUiCards } from "@/utils/card"
import type { Card as UiCard } from "@/types/poker"
import { getSession } from "@/transport/session"
import { useRoom } from "@/store/useRoom"
import { actingPlayer, clearError, clearResult, isMyTurn, myPlayer, mySeat } from "@/store/room"
import { errorText } from "@/utils/errorText"
import {
  bet,
  buyIn,
  check,
  closeRoom,
  enterRoom,
  fold,
  leaveRoom,
  setReady,
  sitDown,
  openFreeEntryVote,
  startHand,
} from "@/store/actions"

interface SeatPlayer {
  id: string
  name: string
  avatar?: string
  points?: number
  isReady?: boolean
}

interface Seat {
  number: number
  player?: SeatPlayer
  isButton?: boolean
}

// useSearchParams 让这棵子树只能在客户端渲染,Next 15 要求它落在 Suspense 边界内,
// 否则整页预渲染报错。故拆成「内层用 searchParams + 外层给边界」两段。
// useSearchParams 让这棵子树只能在客户端渲染,Next 15 要求它落在 Suspense 边界内,
// 否则整页预渲染报错。故拆成「内层用 searchParams + 外层给边界」两段。
function GameView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const roomId = searchParams.get("room") || "dev"

  // 全部牌局状态来自服务器。这一页不发牌、不推进街道、不算底池(见 docs/architecture.md 不变量 1)。
  const state = useRoom()
  const [buyInAmount, setBuyInAmount] = useState<number>(0)
  const [raiseAmount, setRaiseAmount] = useState<number>(0)
  const [seatChoice, setSeatChoice] = useState<number | null>(null)

  useEffect(() => {
    if (!getSession()) {
      router.replace("/")
      return
    }
    let cancelled = false
    void enterRoom(roomId, () => router.replace("/"), () => cancelled).catch(() => {
      if (!cancelled) router.replace("/")
    })
    return () => {
      cancelled = true
      closeRoom()
    }
  }, [roomId, router])

  const me = mySeat(state)
  const mine = myPlayer(state)
  const myTurn = isMyTurn(state)
  const acting = actingPlayer(state)
  const gameStarted = state.handStatus !== null

  /** 把服务器的座位/玩家投影成这一页 JSX 期望的形状。空座渲染成「可入座」。 */
  const seats = useMemo(() => {
    return Array.from({ length: state.maxSeats }, (_, i) => {
      const seatView = state.seats.find((s) => s.seat_position === i)
      const player = seatView
        ? {
            id: seatView.nickname === state.me ? "current" : seatView.nickname,
            name: seatView.nickname,
            points: seatView.points,
            isReady: seatView.status === "ready_to_play" || seatView.status === "playing",
          }
        : undefined
      // 欠一个入局盲(new_here):服务器给的事实,不是本地推的。0084 之前它打完一手就过期,现在
      // user_status_changed 会带着它来。只做展示——「能不能免」由服务器裁决(见下方开票入口的注释)。
      const owesEntry = seatView?.new_here === true
      return { number: i + 1, player, isButton: i === state.buttonPosition, owesEntry }
    })
  }, [state.maxSeats, state.seats, state.me, state.buttonPosition])

  // 欠入局盲的人(= 免盲投票的候选)。纯展示派生,值来自服务器的 new_here,不复算资格。
  const entryOwers = useMemo(
    () => seats.filter((s) => s.player && s.owesEntry).map((s) => s.player!.name),
    [seats],
  )
  const currentPlayers = useMemo(() => seats.filter((s) => s.player).length, [seats])
  const readyPlayers = useMemo(() => seats.filter((s) => s.player?.isReady).length, [seats])
  const allReady = currentPlayers > 0 && readyPlayers === currentPlayers
  const isReady = me?.status === "ready_to_play" || me?.status === "playing"

  const communityCards = useMemo(() => toUiCards(state.board), [state.board])

  /**
   * 手牌只发给自己(your_hole_cards),别人的牌在摊牌前结构上就不存在。
   * 摊牌时 HandShowDown 会带 reveals,那是唯一会出现别人底牌的地方。
   */
  const playerHands = useMemo(() => {
    const hands: Record<number, UiCard[]> = {}
    if (mine && state.yourHoleCards) hands[mine.seat_position + 1] = toUiCards(state.yourHoleCards)
    for (const r of state.reveals) hands[r.seat_position + 1] = toUiCards(r.hole_cards)
    return hands
  }, [mine, state.yourHoleCards, state.reveals])

  const pot = state.pot
  const phase = state.handStatus ?? "preflop"
  /** 本街还要补多少才跟上。服务器给的 last_bet 是本街目标额,减掉自己已投入的。 */
  const callAmount = Math.max(0, state.lastBet - (mine?.bet_amount ?? 0))
  const foldedSeats = useMemo(
    () => new Set(state.players.filter((p) => p.status === "folded").map((p) => p.seat_position + 1)),
    [state.players],
  )
  const currentPlayerFolded = mine?.status === "folded"

  // 座位号在界面上是 1 起,协议里是 0 起,交界处统一在这里换算。
  const handleSeatClick = (seatNumber: number) => {
    if (me || state.seats.some((s) => s.seat_position === seatNumber - 1)) return
    setSeatChoice(seatNumber)
    sitDown(seatNumber - 1, false)
  }

  const handleBuyIn = () => {
    if (!me) return
    buyIn(me.seat_position, buyInAmount || state.buyIn)
  }

  const handleReady = () => {
    if (!me) return
    setReady(!isReady, me.seat_position)
  }

  const handleStartGame = () => {
    if (!me) return
    startHand(me.seat_position)
  }

  /**
   * 跟注、加注、all-in 在协议上都是 bet,区别只在金额,且 bet_amount 是**本街目标总额**
   * 而不是增量(见 service/docs/rules.md ②)。
   */
  const handleAction = (action: "fold" | "check" | "call" | "raise" | "all-in") => {
    if (!myTurn || !mine) return
    switch (action) {
      case "fold":
        fold()
        break
      case "check":
        check()
        break
      case "call":
        bet(state.lastBet)
        break
      case "raise":
        bet(raiseAmount || state.lastBet + state.bigBlind)
        break
      case "all-in":
        bet(mine.points + mine.bet_amount)
        break
    }
  }

  const handleLeave = () => {
    leaveRoom()
    closeRoom()
    router.replace("/lobby")
  }

  return (
    <div className="min-h-screen relative overflow-hidden bg-background text-foreground p-4 md:p-8">
      {/* Background suits to match lobby */}
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

      <div className="relative z-10 mx-auto flex max-w-6xl flex-col gap-6">
        {/* Top bar: Game info and controls */}
        <Card className="bg-card/95 border-primary/30 flex items-center justify-between border-2 p-4 shadow-xl">
          <div className="flex items-center gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Game Room</p>
              <p className="text-lg font-semibold text-primary">德州扑克主桌</p>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Ready:</span>
              <span className="font-bold text-primary">{readyPlayers} / {currentPlayers}</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {/* 街道推进由服务器的 hand_status_changed 驱动,本地不再有手动按钮 */}
            {/* 未入座:先在桌上点一个空位坐下(sit_down),这一步原 mock 版跳过了。 */}
            {!me && (
              <span className="px-3 py-1.5 rounded-lg bg-sky-500/20 border border-sky-500/50 text-sky-300 text-xs">
                观战中 · 点桌上空位入座
              </span>
            )}
            {/* 已入座但桌上没筹码:必须先买入才能准备(buy_in)。 */}
            {me && me.points === 0 && (
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  value={buyInAmount || state.buyIn}
                  onChange={(e) => setBuyInAmount(Number(e.target.value))}
                  className="h-8 w-24 rounded border border-primary/40 bg-black/40 px-2 text-sm"
                />
                <Button size="sm" onClick={handleBuyIn} className="bg-amber-600 hover:bg-amber-700 text-white">
                  买入
                </Button>
              </div>
            )}
            {/*
              开票入口:只要在两手之间就给,**仍然不预判能不能成**。

              0084 之后 new_here 是可靠的了(服务端重标时会广播),所以「有没有候选」现在能如实显示,
              下面的提示就是照它写的。但另一半——「有没有合格投票人」(非 new_here 且已准备)——依然是
              规则,前端不算:算它就是复算服务器规则,而这正是前端不变量 1 禁止的。

              所以决定不变:入口照给,发出去让服务器裁决,被拒时把 CANNOT_OPEN_VOTE 翻成人话。
              变的只是现在能顺带告诉用户「桌上有谁在等入局」,让人知道值不值得点。
            */}
            {!state.freeEntryVote && me && state.handStatus === null && (
              <Button
                size="sm"
                variant="outline"
                onClick={openFreeEntryVote}
                data-testid="open-free-entry-vote"
                title={
                  entryOwers.length > 0
                    ? `等入局:${entryOwers.join('、')}`
                    : '当前没人在等入局,服务器多半会拒'
                }
                className="border-amber-500/50 text-amber-300 text-xs"
              >
                发起免盲投票{entryOwers.length > 0 ? `(${entryOwers.length} 人等入局)` : ''}
              </Button>
            )}
            <RoomConfig />
            {/* 后端只回机器码,文案归前端映射(见 service/docs/error.md)。点一下消掉。 */}
            {state.lastError && (
              <button
                type="button"
                onClick={clearError}
                title={state.lastError.detail ?? state.lastError.code}
                className="px-3 py-1.5 rounded-lg bg-red-500/20 border border-red-500/50 text-red-300 text-xs"
              >
                {errorText(state.lastError.code)}
              </button>
            )}
            {!isReady ? (
              <Button
                onClick={handleReady}
                disabled={!me || me.points === 0}
                className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold"
              >
                Ready
              </Button>
            ) : (
              <div className="px-4 py-2 bg-green-500/20 border border-green-500/50 rounded-lg">
                <span className="text-green-400 font-bold text-sm">✓ Ready</span>
              </div>
            )}
            {allReady && !gameStarted && (
              <Button
                onClick={handleStartGame}
                className="bg-green-600 hover:bg-green-700 text-white font-bold"
              >
                Start Game
              </Button>
            )}
            {gameStarted && (
              <span className="px-3 py-1.5 rounded-lg bg-emerald-500/20 border border-emerald-500/50 text-emerald-400 font-semibold text-sm">
                In game
              </span>
            )}
            {/*
              这一页唯一的出口,故意不放别的跳转(设置、手牌历史都只在大厅进)。
              理由:任何离开本页的导航都会断掉 ws,而在座时断线是按「保座 + 筹码锁在桌上」
              处理的(见 service/docs/connection.md),要等清理超时才释放;走这个按钮才是
              LeaveRoom 那条干净路径——退分离桌、筹码结算回全局积分。
            */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleLeave}
              title="退分离桌回大厅:桌上筹码结算回全局积分,座位释放"
              className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
            >
              离开牌桌
            </Button>
          </div>
        </Card>

        {/* Gaming table with background image */}
        <Card className="relative flex flex-1 flex-col justify-between gap-6 overflow-hidden bg-card/95 border-2 border-primary/30 p-5 shadow-2xl">
          {/* Gaming table background image container - ADJUST SIZE HERE */}
          {/* Change min-h, max-w, or aspect ratio to adjust background box size */}
          <div 
            className="relative w-full mx-auto"
            style={{
              minHeight: "700px", // ADJUST: Change this to make background taller/shorter
              maxWidth: "1300px",  // ADJUST: Change this to make background wider/narrower
              aspectRatio: "16/9",  // ADJUST: Change ratio (e.g., "4/3", "21/9", "16/10")
            }}
          >
            <Image
              src={gamingTableBg}
              alt="Gaming table"
              fill
              className="object-contain" // Changed to object-contain to see full image
              priority
              quality={90}
            />
            {/* Dark overlay for better contrast */}
            <div className="absolute inset-0 bg-black/20"></div>

            {/* Community cards: 5 fixed slots – flop = slots 0–2 (never move), turn/river append in 3–4 */}
            {gameStarted && (
              <div
                className="absolute left-[63%] top-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2"
                style={{ transform: "translate(-50%, -50%)" }}
              >
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-20 w-14 shrink-0">
                    {communityCards[i] ? (
                      <PokerCard card={communityCards[i]} size="md" className="h-full w-full" />
                    ) : null}
                  </div>
                ))}
              </div>
            )}

            {/* Player info cards - MANUAL POSITIONING */}
            {/* Each card position is set manually using percentages (0-100%) */}
            {/* left: 0% = far left, 50% = center, 100% = far right */}
            {/* top: 0% = top, 50% = center, 100% = bottom */}
            {seats.map((seat) => {
              // Seat positions: bottom row (5,6,7) aligned at same top; user (6) centered
              const manualPositions: Record<number, { left: number; top: number }> = {
                1: { left: 25, top: 11 },
                2: { left: 50, top: 9 },
                3: { left: 77, top: 12 },
                4: { left: 95, top: 40 },
                5: { left: 72, top: 84 },  // User – aligned with 5 & 7
                6: { left: 50, top: 84 },
                7: { left: 28, top: 84 },
                8: { left: 5, top: 60 },
                9: { left: 7, top: 30 },
              }

              const position = manualPositions[seat.number] || { left: 50, top: 50 }

              // 空座要能点:观战者靠它入座(sit_down)。原 mock 版直接不渲染空座,
              // 于是进房后根本没有入座的入口——0079 的浏览器测试才发现这条死路。
              if (!seat.player) {
                return (
                  <button
                    key={seat.number}
                    type="button"
                    data-empty-seat={seat.number}
                    onClick={() => handleSeatClick(seat.number)}
                    disabled={!!me}
                    title={me ? "你已入座" : `坐到 ${seat.number} 号位`}
                    className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed border-primary/50 bg-black/40 px-3 py-2 text-xs text-primary/80 hover:bg-primary/20 disabled:opacity-30 disabled:hover:bg-black/40"
                    style={{ left: `${position.left}%`, top: `${position.top}%` }}
                  >
                    入座 {seat.number}
                  </button>
                )
              }

              // 只有自己的座位(以及摊牌后被亮牌的人)在 playerHands 里有条目,别人一律没有——
              // 这正是隐私边界。所以必须兜 []:原 mock 给所有座位都发了牌,直接读 .length 不会崩,
              // 换成真实数据后其余座位是 undefined,页面会整个白屏(0080 由浏览器测试发现)。
              const hand = (gameStarted ? playerHands[seat.number] : []) ?? []
              const isCurrent = seat.player.id === "current"

              return (
                <div
                  key={seat.number}
                  className={cn(
                    "absolute flex flex-col items-center",
                    !isCurrent && "gap-0.5"
                  )}
                  style={{
                    left: `${position.left}%`,
                    top: `${position.top}%`,
                    transform: "translate(-50%, -50%)",
                    zIndex: 20,
                    minWidth: isCurrent ? "120px" : "100px",
                  }}
                >
                  {/* 欠一个入局盲:标出来,否则「为什么他没被发牌」在界面上无从解释(值由服务器给) */}
                  {seat.owesEntry && (
                    <span
                      data-owes-entry={seat.number}
                      title="下一手需付一个大盲才能入局(或等大盲位轮到他),见免盲投票"
                      className="absolute -top-3 z-30 rounded-full border border-amber-500/60 bg-black/80 px-1.5 py-0.5 text-[10px] leading-none text-amber-300"
                    >
                      等入局
                    </span>
                  )}
                  {isCurrent ? (
                    <>
                      {/* Current player: cards on top, half covered by info bar; face-down if folded */}
                      {gameStarted && hand.length === 2 && (
                        <div className="relative z-0 flex justify-center -mb-10">
                          <div className="flex items-end min-h-[32px]">
                            {currentPlayerFolded ? (
                              <>
                                <PokerCard
                                  card={hand[0]}
                                  isHidden
                                  size="lg"
                                  className="origin-bottom -mr-6 -rotate-[10deg] opacity-90"
                                />
                                <PokerCard
                                  card={hand[1]}
                                  isHidden
                                  size="lg"
                                  className="origin-bottom -ml-6 rotate-[10deg] opacity-90"
                                />
                              </>
                            ) : (
                              <>
                                <PokerCard
                                  card={hand[0]}
                                  size="lg"
                                  cornerLabel
                                  className="origin-bottom -mr-6 -rotate-[10deg]"
                                />
                                <PokerCard
                                  card={hand[1]}
                                  size="lg"
                                  cornerLabel
                                  className="origin-bottom -ml-6 rotate-[10deg]"
                                />
                              </>
                            )}
                          </div>
                        </div>
                      )}
                      <div className="relative z-10">
                        {currentPlayerFolded && (
                          <div className="absolute -top-1 left-1/2 z-20 -translate-x-1/2 rounded bg-slate-600 px-2 py-0.5 text-[10px] font-bold text-white">
                            Folded
                          </div>
                        )}
                        <PlayerInfoCard
                          player={seat.player}
                          isButton={seat.isButton}
                          isCurrentPlayer={true}
                          className={cn("scale-105", currentPlayerFolded && "opacity-75")}
                        />
                      </div>
                    </>
                  ) : (
                    <>
                      <PlayerInfoCard
                        player={seat.player}
                        isButton={seat.isButton}
                        isCurrentPlayer={false}
                        className="scale-90"
                      />
                      {gameStarted && hand.length === 2 && (
                        <div className="flex origin-center -mr-2">
                          <PokerCard card={hand[0]} isHidden size="lg" className="-mr-2" />
                          <PokerCard card={hand[1]} isHidden size="lg" className="-ml-2" />
                        </div>
                      )}
                    </>
                  )}
                </div>
              )
            })}
          </div>

          {/* Game mode: compact action bar (hidden when current player folded) */}
          {gameStarted && !currentPlayerFolded && (
            <div className="absolute left-0 right-0 z-30 flex flex-col items-center gap-2 pb-2 bottom-6">
              {/* 按钮在别人回合是灰的,必须说清在等谁,不然用户只看到一排点不动的按钮。 */}
              <span className="rounded-full bg-black/60 px-3 py-1 text-xs text-amber-200">
                {myTurn ? "轮到你行动" : `等待 ${acting?.nickname ?? "…"} 行动`}
              </span>
              <div className="flex items-center gap-1.5 rounded-full bg-black/70 backdrop-blur-sm border border-white/10 py-1.5 px-2 shadow-lg">
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => handleAction("fold")}
                  disabled={!myTurn}
                  className="h-7 rounded-full px-3 text-xs font-semibold shadow-sm min-w-0"
                >
                  Fold
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => handleAction("check")}
                  disabled={!myTurn}
                  className="h-7 rounded-full px-3 text-xs font-semibold border-white/10 min-w-0"
                >
                  Check
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleAction("call")}
                  disabled={!myTurn}
                  className="h-7 rounded-full px-3 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white min-w-0"
                >
                  Call {callAmount}
                </Button>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    min={callAmount * 2}
                    value={raiseAmount}
                    onChange={(e) => setRaiseAmount(Number(e.target.value) || raiseAmount)}
                    className="w-14 rounded-full border border-amber-500/40 bg-black/50 px-2 py-1 text-center text-xs font-semibold text-amber-200 focus:outline-none focus:ring-1 focus:ring-amber-500"
                  />
                  <Button
                    size="sm"
                    onClick={() => handleAction("raise")}
                  disabled={!myTurn}
                    className="h-7 rounded-full px-3 text-xs font-semibold bg-amber-600 hover:bg-amber-700 text-white min-w-0"
                  >
                    Raise
                  </Button>
                </div>
                <Button
                  size="sm"
                  onClick={() => handleAction("all-in")}
                  disabled={!myTurn}
                  className="h-7 rounded-full px-3 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white min-w-0"
                >
                  All-in
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>

      {/*
        私聊。位置和大厅完全一致(右下角浮标 → 右侧抽屉),这是有意的:
        私聊跨房间存在、有未读有已读,房聊随房销毁、不在场就是错过,两者不能被当成一回事。
        所以私聊固定占右缘,**房间聊天将来要另起一处**(牌桌卡片内侧或左缘),不许也做成右侧抽屉。
        目前这一页还没有任何房聊 UI(state.chat / sendChat 都还没接),见交付说明。
      */}
      <ConnectionBanner />
      <FreeEntryVote />
      <HandResult onDismiss={clearResult} />
      <DmDrawer />
    </div>
  )
}


export default function GamePage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-background" />}>
      <GameView />
    </Suspense>
  )
}
