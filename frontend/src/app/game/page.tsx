"use client"

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import PlayerInfoCard from "@/components/PlayerInfoCard"
import PokerCard from "@/components/PokerCard"
import ConnectionBanner from "@/components/ConnectionBanner"
import FreeEntryVote from "@/components/FreeEntryVote"
import RoomConfig from "@/components/RoomConfig"
import HandResult from "@/components/HandResult"
import TableChat from "@/components/TableChat"
import AiPlayers from "@/components/AiPlayers"
import ActionPanel from "@/components/table/ActionPanel"
import { ChipStack, FlyingChips } from "@/components/table/Chips"
import { TABLE_SEAT_SPOTS } from "@/components/tableSeats"
import { ALLIN_CLASS, levelClass, levelName } from "@/components/table/betLevel"
import { positionNames } from "@/utils/positions"
import { playCheck, playChips, playChipSweep, playDeal, playFold, playYourTurn, setSoundEnabled, soundEnabled } from "@/utils/sound"
import { formatChips } from "@/utils/poker"
import Image from "next/image"
import gamingTableBg from "@/pics/game-table.jpg"
import { cn } from "@/lib/utils"
import { toUiCards } from "@/utils/card"
import type { Card as UiCard } from "@/types/poker"
import { getSession } from "@/transport/session"
import { useRoom } from "@/store/useRoom"
import { actingPlayer, clearError, clearResult, getRoomState, isMyTurn, myPlayer, mySeat, type LastAction } from "@/store/room"
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

/** 底池画在桌心、公共牌正上方(% of the table box)。 */
const POT_SPOT = { left: 50, top: 36 }

/**
 * 座位旁的动作气泡:文案 + 颜色。加注按本街第几注越来越红:Bet → Raise → 3-Bet → 4-Bet → All-in。
 */
function actionBubble(a: LastAction): { text: string; cls: string } {
  switch (a.kind) {
    case "fold":
      return { text: "Fold", cls: "bg-slate-600/90 text-slate-100" }
    case "check":
      return { text: "Check", cls: "bg-sky-700/90 text-white" }
    case "call":
      return { text: `Call ${a.amount}`, cls: "bg-emerald-600/90 text-white" }
    case "allin":
      return { text: `All-in ${a.amount}`, cls: ALLIN_CLASS }
    default:
      return { text: `${levelName(a.level)} ${a.amount}`, cls: levelClass(a.level) }
  }
}

// useSearchParams 让这棵子树只能在客户端渲染,Next 15 要求它落在 Suspense 边界内,
// 否则整页预渲染报错。故拆成「内层用 searchParams + 外层给边界」两段。
function GameView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const roomId = searchParams.get("room") || "dev"

  // 全部牌局状态来自服务器。这一页不发牌、不推进街道、不算底池(见 docs/architecture.md 不变量 1)。
  const state = useRoom()
  const [buyInAmount, setBuyInAmount] = useState<number>(0)

  useEffect(() => {
    if (!getSession()) {
      router.replace("/")
      return
    }
    let cancelled = false
    // 被顶替 / 会话失效时带上原因跳回登录页,让人知道自己为什么被踢出来(见 transport/ws.ts)。
    void enterRoom(roomId, (reason) => router.replace(`/?reason=${reason}`), () => cancelled).catch(() => {
      if (!cancelled) router.replace("/")
    })
    return () => {
      cancelled = true
      closeRoom()
    }
  }, [roomId, router])

  const me = mySeat(state)
  const mine = myPlayer(state)

  // 从大厅点了某个空座进来(?seat=N):进房拿到快照后替你坐下一次。座位已被别人占了就不动,留在观战。
  const wantSeat = Number(searchParams.get("seat")) || null
  const autoSeated = useRef(false)
  useEffect(() => {
    if (autoSeated.current || !wantSeat || state.room !== roomId || !state.me || me) return
    autoSeated.current = true
    if (!state.seats.some((s) => s.seat_position === wantSeat - 1)) sitDown(wantSeat - 1, false)
  }, [wantSeat, state.room, state.me, state.seats, roomId, me])
  const myTurn = isMyTurn(state)
  const acting = actingPlayer(state)
  const gameStarted = state.handStatus !== null
  /**
   * 结算展示期:手牌已经结束,但摊牌结果还留在桌上,直到下一手开始。
   *
   * `hand_show_down` 与 `hand_ended` 是**同一批事件**(服务端 `_settle_and_end` 一次产出),
   * 所以只按 `gameStarted` 渲染的话,亮出来的牌在 6 毫秒后就随整桌一起卸载(0105 实测)。
   *
   * 判据用 `reveals` 而不是另立一个状态:它只由 `hand_show_down` 写入,而清空它的只有 `hand_started`、
   * `state_snapshot` 与 `resetRoom`(离房/失去鉴权,见 store/room.ts)——全都意味着「没有上一手可看了」。
   * 所以「非空」精确等于「这一手摊过牌且新一手还没开始」。
   */
  const showingShowdown = !gameStarted && state.reveals.length > 0
  /** 桌上「有一手牌可谈」没有:进行中的手牌,或结算展示期。牌面、Folded 标与变暗都跟它走。 */
  const tableCardsVisible = gameStarted || showingShowdown

  /** 把服务器的座位/玩家投影成这一页 JSX 期望的形状。空座渲染成「可入座」。 */
  const seats = useMemo(() => {
    // 在手玩家的实时筹码。合并口径照抄服务器自己的快照投影(reduce.py `_state_snapshot`):
    // **在手取 Player.points,不在手取 Seat.points**(BUG-20)。结算展示期显示 hand_ended.stacks 写进 seats 的值。
    const inHand = new Map(gameStarted ? state.players.map((p) => [p.nickname, p.points]) : [])
    return Array.from({ length: state.maxSeats }, (_, i) => {
      const seatView = state.seats.find((s) => s.seat_position === i)
      const player = seatView
        ? {
            id: seatView.nickname === state.me ? "current" : seatView.nickname,
            name: seatView.nickname,
            points: inHand.get(seatView.nickname) ?? seatView.points,
            isReady: seatView.status === "ready_to_play" || seatView.status === "playing",
          }
        : undefined
      // 欠一个入局盲(new_here):服务器给的事实,只做展示——「能不能免」由服务器裁决。
      const owesEntry = seatView?.new_here === true
      return { number: i + 1, player, isButton: i === state.buttonPosition, owesEntry }
    })
  }, [state.maxSeats, state.seats, state.me, state.buttonPosition, state.players, gameStarted])

  // 欠入局盲的人(= 免盲投票的候选)。纯展示派生,值来自服务器的 new_here,不复算资格。
  const entryOwers = useMemo(
    () => seats.filter((s) => s.player && s.owesEntry).map((s) => s.player!.name),
    [seats],
  )
  const currentPlayers = useMemo(() => seats.filter((s) => s.player).length, [seats])
  const readyPlayers = useMemo(() => seats.filter((s) => s.player?.isReady).length, [seats])
  // 开一手至少要 2 个准备好的玩家(服务器规则,一个人没法打)。只剩自己时不给开局按钮,改提示去加 AI。
  const allReady = readyPlayers >= 2 && readyPlayers === currentPlayers
  const isReady = me?.status === "ready_to_play" || me?.status === "playing"

  const communityCards = useMemo(() => toUiCards(state.board), [state.board])

  /**
   * 发公共牌的节奏,像真牌桌:翻牌一次三张,停一下再转牌,再停一下河牌。服务器在全下跑牌时会
   * 一次给齐 5 张,这里也照样一街一街地亮。只是**延后显示**服务器已经给的牌,不改内容。
   * 牌没亮完之前:不给行动按钮、不亮对手底牌、不弹结算,免得画面跑到牌前面。
   */
  const [shownBoard, setShownBoard] = useState(state.board.length)
  useEffect(() => {
    const len = state.board.length
    if (len < shownBoard) {
      setShownBoard(len) // 新一手 / 快照:直接对齐
      return
    }
    if (len === shownBoard) return
    const next = shownBoard < 3 ? 3 : shownBoard + 1
    const t = setTimeout(() => setShownBoard(Math.min(next, len)), shownBoard === 0 ? 650 : 1000)
    return () => clearTimeout(t)
  }, [state.board.length, shownBoard])
  const boardDone = shownBoard >= state.board.length

  /** 这一手每个人的位置(BTN / SB / BB / UTG … CO),按实际发到牌的人数和庄位算,纯展示。 */
  const positions = useMemo(
    () =>
      tableCardsVisible
        ? positionNames(state.players.map((p) => p.seat_position), state.buttonPosition, state.maxSeats)
        : new Map<number, string>(),
    [tableCardsVisible, state.players, state.buttonPosition, state.maxSeats],
  )

  // ── 音效:下注/跟注的筹码声、过牌敲桌、弃牌、发牌、轮到自己的提示音 ──
  const [soundOn, setSoundOn] = useState(true)
  useEffect(() => setSoundOn(soundEnabled()), [])
  const prevActions = useRef(state.lastActions)
  useEffect(() => {
    const prev = prevActions.current
    prevActions.current = state.lastActions
    for (const [nick, a] of Object.entries(state.lastActions)) {
      const before = prev[nick]
      if (before && before.kind === a.kind && before.amount === a.amount) continue
      if (a.kind === "fold") playFold()
      else if (a.kind === "check") playCheck()
      else playChips(a.amount, state.bigBlind)
    }
  }, [state.lastActions, state.bigBlind])
  const prevShown = useRef(shownBoard)
  useEffect(() => {
    if (shownBoard > prevShown.current) playDeal(shownBoard - prevShown.current)
    prevShown.current = shownBoard
  }, [shownBoard])
  const hadHole = useRef(false)
  useEffect(() => {
    const has = state.yourHoleCards !== null
    if (has && !hadHole.current) playDeal(2)
    hadHole.current = has
  }, [state.yourHoleCards])

  /**
   * 视角:不管坐哪个座位,自己总画在正下方那把椅子(6 号位的点位),其余座位按顺时针顺序跟着转。
   * 只转**画的位置**;座位号(data-seat、入座 N)仍是服务器的 seat_position + 1。
   */
  const mySeatNumber = me ? me.seat_position + 1 : null
  const spotOf = useCallback(
    (seatNumber: number) => {
      const n = state.maxSeats
      const shown = mySeatNumber !== null && n === 9 ? ((seatNumber - mySeatNumber + 5 + 9) % 9) + 1 : seatNumber
      return TABLE_SEAT_SPOTS[shown] ?? { left: 50, top: 50 }
    },
    [state.maxSeats, mySeatNumber],
  )
  /**
   * 还是盲注的那份下注标出来(SB 蓝 / BB 紫),和主动下注区分开:翻牌前、这个人还没自己行动过、
   * 位置是小盲/大盲。纯展示,金额照服务器的 bet_amount。
   */
  const blindTag = (p: { nickname: string; seat_position: number }): "SB" | "BB" | undefined => {
    if (state.handStatus !== "pre_flop" || state.lastActions[p.nickname]) return undefined
    const pos = positions.get(p.seat_position)
    if (pos === "SB" || pos === "BTN/SB") return "SB"
    if (pos === "BB") return "BB"
    return undefined
  }

  /** 下注筹码摆在座位与桌心之间;正下方那个座位手里有大牌,筹码再往里放一点。 */
  const betSpotOf = useCallback(
    (seatNumber: number) => {
      const s = spotOf(seatNumber)
      const k = s.top > 80 && Math.abs(s.left - 50) < 5 ? 0.5 : 0.42
      return { left: s.left + (50 - s.left) * k, top: s.top + (47 - s.top) * k }
    },
    [spotOf],
  )

  /** 自己的底牌(`your_hole_cards` 私发给本人)。别人的牌只可能来自摊牌,见 `revealedHands`。 */
  const myHand = useMemo(
    () => (mine && state.yourHoleCards ? toUiCards(state.yourHoleCards) : null),
    [mine, state.yourHoleCards],
  )

  /**
   * 摊牌亮出来的底牌,**按昵称索引**。结算展示期跨越两手之间,那段时间座位会易主,
   * 按座位号索引就会把上一手某人的底牌挂到新占座的人名下。昵称全局唯一且房内不可改名。
   */
  const revealedHands = useMemo(() => {
    const byNick = new Map<string, UiCard[]>()
    for (const r of state.reveals) byNick.set(r.nickname, toUiCards(r.hole_cards))
    return byNick
  }, [state.reveals])

  const pot = state.pot

  // ── 筹码动画:换街时各家下注滑进底池;结算时底池滑向赢家。纯展示,金额都是服务器给的。 ──
  type Flight = { id: number; from: { left: number; top: number }; to: { left: number; top: number }; amount: number; delay: number }
  const [flights, setFlights] = useState<Flight[]>([])
  const flightId = useRef(0)
  const launch = useCallback((items: Omit<Flight, "id">[]) => {
    if (items.length === 0) return
    const withIds = items.map((f) => ({ ...f, id: ++flightId.current }))
    setFlights((cur) => [...cur, ...withIds])
    const ids = new Set(withIds.map((f) => f.id))
    const longest = Math.max(...items.map((f) => f.delay)) + 900
    setTimeout(() => setFlights((cur) => cur.filter((f) => !ids.has(f.id))), longest)
  }, [])

  const prevBets = useRef<{ status: string | null; bets: { seat: number; amount: number }[] }>({ status: null, bets: [] })
  useEffect(() => {
    const prev = prevBets.current
    if (prev.status !== null && prev.status !== state.handStatus && prev.bets.length > 0) {
      playChipSweep(prev.bets.reduce((n, b) => n + b.amount, 0), state.bigBlind)
      launch(prev.bets.map((b, i) => ({ from: betSpotOf(b.seat), to: POT_SPOT, amount: b.amount, delay: i * 40 })))
    }
    prevBets.current = {
      status: state.handStatus,
      bets: state.handStatus === null ? [] : state.players.filter((p) => p.bet_amount > 0).map((p) => ({ seat: p.seat_position + 1, amount: p.bet_amount })),
    }
  }, [state.handStatus, state.players, state.bigBlind, betSpotOf, launch])

  const paidResult = useRef<unknown>(null)
  useEffect(() => {
    const result = state.lastResult
    if (!result || !boardDone || paidResult.current === result) return
    paidResult.current = result
    setTimeout(() => playChipSweep(result.winnings.reduce((n, w) => n + w.amount, 0), state.bigBlind), 450)
    launch(
      result.winnings
        .map((w) => {
          const seat = state.seats.find((s) => s.nickname === w.nickname)
          return seat ? { from: POT_SPOT, to: spotOf(seat.seat_position + 1), amount: w.amount, delay: 450 } : null
        })
        .filter((f): f is Omit<Flight, "id"> => f !== null),
    )
  }, [state.lastResult, boardDone, state.seats, state.bigBlind, spotOf, launch])
  /** 本街还要补多少才跟上。服务器给的 last_bet 是本街目标额,减掉自己已投入的。 */
  const callAmount = Math.max(0, state.lastBet - (mine?.bet_amount ?? 0))
  const currentPlayerFolded = mine?.status === "folded"
  const showActions = gameStarted && !!mine && !currentPlayerFolded
  /** 每次轮到新的人行动就变,让加注框回到服务器给的下限。 */
  const turnKey = `${state.handStatus}-${state.actingPosition}-${state.lastBet}-${state.pot}`

  // 座位号在界面上是 1 起,协议里是 0 起,交界处统一在这里换算。
  const handleSeatClick = (seatNumber: number) => {
    if (me || state.seats.some((s) => s.seat_position === seatNumber - 1)) return
    sitDown(seatNumber - 1, false)
  }

  const handleBuyIn = () => {
    if (!me) return
    buyIn(me.seat_position, buyInAmount || state.buyIn)
  }

  // 跟注、加注、all-in 在协议上都是 bet,区别只在金额,且 bet_amount 是**本街目标总额**而不是增量
  // (见 service/docs/rules.md ②)。按钮合法与否由服务器裁定,前端不预判。
  const canAct = myTurn && boardDone
  const wasMyTurn = useRef(false)
  useEffect(() => {
    if (canAct && !wasMyTurn.current) playYourTurn()
    wasMyTurn.current = canAct
  }, [canAct])

  // ── 自动下一手:一手结束后自动重新准备,5 秒后开下一手(像线上牌桌那样连着打)──
  // 服务器在每手结束时把所有人放回「未准备」(SITTING_IN),所以要先替你点回准备。
  // 谁来发 start_hand:座位号最小的那位已准备玩家(AI 那边用同一条规则),避免几个客户端抢着发;
  // 他迟迟不发(比如关了自动)就 2.5 秒后谁都可以发,多发的那条被服务器拒,错误提示自动收掉。
  // 浏览器自动化(e2e)下默认关:用例自己控制什么时候开局。
  const [autoNext, setAutoNext] = useState(true)
  useEffect(() => {
    try {
      const saved = localStorage.getItem("poker.autoNext")
      if (saved !== null) setAutoNext(saved === "1")
      else if (navigator.webdriver) setAutoNext(false)
    } catch {
      /* storage blocked: keep the default */
    }
  }, [])
  const toggleAutoNext = (on: boolean) => {
    setAutoNext(on)
    try {
      localStorage.setItem("poker.autoNext", on ? "1" : "0")
    } catch {
      /* storage blocked: setting lasts for this page only */
    }
  }
  const handOver = !gameStarted && state.lastResult !== null && boardDone
  const autoSentAt = useRef(0)
  const reReadyFor = useRef<unknown>(null)
  useEffect(() => {
    if (!autoNext || !handOver || !me || me.points === 0 || me.status !== "sitting_in") return
    if (reReadyFor.current === state.lastResult) return
    reReadyFor.current = state.lastResult
    autoSentAt.current = Date.now()
    setReady(true, me.seat_position)
  }, [autoNext, handOver, me, state.lastResult])

  const [countdown, setCountdown] = useState<number | null>(null)
  useEffect(() => {
    if (!autoNext || !handOver || !mySeat(getRoomState())) {
      setCountdown(null)
      return
    }
    const startAt = Date.now() + 5000
    let sent = false
    const tick = setInterval(() => {
      const now = Date.now()
      setCountdown(Math.max(0, Math.ceil((startAt - now) / 1000)))
      if (now < startAt || sent) return
      const st = getRoomState()
      const mine = mySeat(st)
      if (st.handStatus !== null || !mine) return
      const ready = st.seats.filter((x) => x.status === "ready_to_play").map((x) => x.seat_position).sort((a, b) => a - b)
      if (ready.length < 2 || mine.status !== "ready_to_play") return
      if (ready[0] === mine.seat_position || now - startAt > 2500) {
        sent = true
        autoSentAt.current = now
        startHand(mine.seat_position)
      }
    }, 250)
    return () => clearInterval(tick)
  }, [autoNext, handOver, state.lastResult])

  // 自动发出的准备/开局被服务器拒(别人先开了、状态已变)是预料之中的,不弹错误。
  useEffect(() => {
    const e = state.lastError
    const expected = ["HAND_IN_PROGRESS", "NOT_READY", "NOT_ENOUGH_PLAYERS", "INVALID_STATUS_TRANSITION"]
    if (e && Date.now() - autoSentAt.current < 3000 && expected.includes(e.code)) clearError()
  }, [state.lastError])
  const ifMyTurn = (fn: () => void) => () => {
    if (canAct && mine) fn()
  }

  const handleLeave = () => {
    leaveRoom()
    closeRoom()
    router.replace("/lobby")
  }

  return (
    <div className="app-ui relative flex min-h-screen flex-col overflow-hidden bg-background text-foreground">
      {/* 背景花色,与大厅/登录页同一套 */}
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
      {/* ── 顶栏 ── */}
      <header className="relative z-40 flex items-center justify-between gap-4 border-b border-line/70 bg-room-deep/70 px-4 py-2.5 backdrop-blur md:px-6">
        <div className="flex min-w-0 items-center gap-4">
          {/*
            这一页唯一的出口,故意不放别的跳转。任何离开本页的导航都会断掉 ws,而在座时断线是按
            「保座 + 筹码锁在桌上」处理的,要等清理超时才释放;走这个按钮才是 LeaveRoom 那条干净路径。
          */}
          <button
            type="button"
            onClick={handleLeave}
            title="退分离桌回大厅:桌上筹码结算回全局积分,座位释放"
            className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm text-ink hover:border-muted-ink"
          >
            <span aria-hidden>‹</span> 离开牌桌
          </button>
          <div className="min-w-0">
            <p className="truncate font-condensed text-xl font-semibold leading-none text-ink">{state.room ?? roomId}</p>
            <p className="mt-1 text-xs text-muted-ink">
              盲注 <span className="num text-ink">{state.smallBlind}/{state.bigBlind}</span>
              <span className="mx-2 text-line">|</span>
              在座 <span className="num text-ink">{currentPlayers}/{state.maxSeats}</span>
              <span className="mx-2 text-line">|</span>
              已准备 <span className="num text-ink">{readyPlayers}</span>
            </p>
          </div>
          {gameStarted ? (
            <span className="flex items-center gap-1.5 rounded-full bg-call/15 px-3 py-1 text-xs font-semibold text-call">
              <span className="h-1.5 w-1.5 rounded-full bg-call" /> In game
            </span>
          ) : (
            <span className="rounded-full bg-panel-raised px-3 py-1 text-xs text-muted-ink">等待开局</span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* 后端只回机器码,文案归前端映射(见 service/docs/error.md)。点一下消掉。 */}
          {state.lastError && (
            <button
              type="button"
              onClick={clearError}
              title={state.lastError.detail ?? state.lastError.code}
              data-testid="action-error"
              className="max-w-xs truncate rounded-lg border border-fold/50 bg-fold/15 px-3 py-1.5 text-xs font-semibold text-[#ff9aa2]"
            >
              {errorText(state.lastError.code)}
            </button>
          )}
          {/*
            开票入口:只要在两手之间就给,**不预判能不能成**。「有没有候选」(new_here)是服务器给的
            可靠事实,可以如实显示;「有没有合格投票人」是规则,前端不算,发出去让服务器裁决。
          */}
          {!state.freeEntryVote && me && state.handStatus === null && (
            <button
              type="button"
              onClick={openFreeEntryVote}
              data-testid="open-free-entry-vote"
              title={entryOwers.length > 0 ? `等入局:${entryOwers.join("、")}` : "当前没人在等入局,服务器多半会拒"}
              className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:border-raise/60"
            >
              发起免盲投票{entryOwers.length > 0 ? `(${entryOwers.length} 人等入局)` : ""}
            </button>
          )}
          {/* 开发时才有:往这桌加 AI 对手,一个人也能打一手完整的牌(scripts/bots.mjs) */}
          {process.env.NODE_ENV === "development" && state.room && (
            <AiPlayers room={state.room} me={state.me} freeSeats={state.maxSeats - currentPlayers} />
          )}
          <button
            type="button"
            onClick={() => {
              setSoundEnabled(!soundOn)
              setSoundOn(!soundOn)
            }}
            aria-label={soundOn ? "关闭音效" : "打开音效"}
            title={soundOn ? "音效:开" : "音效:关"}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-panel text-base hover:border-muted-ink"
          >
            {soundOn ? "🔊" : "🔇"}
          </button>
          <RoomConfig />
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 md:flex-row">
        {/*
          左侧:结算 + 牌桌聊天。房聊按约定放在牌桌左缘(不是右侧抽屉)。
          结算面板放在这里而不是桌面上:旧版浮在桌面中间,要量着避开公共牌(0105),窄屏还会压住。
        */}
        <aside className="order-2 flex w-full shrink-0 flex-col gap-3 md:order-1 md:h-[calc(100vh-93px)] md:w-72">
          {/* 牌亮完再出结算,免得结果跑在河牌前面 */}
          {boardDone && <HandResult onDismiss={clearResult} />}
          <TableChat className="h-80 md:h-auto md:flex-1" />
        </aside>

        <div className="order-1 flex min-w-0 flex-1 flex-col items-center gap-3 md:order-2">
          <div 
            className="relative w-full mx-auto"
            style={{
              // Fit the viewport: the action dock sits under the table instead of on top of it,
              // so the box is sized from the height left over (header + dock ≈ 250px).
              maxWidth: "1300px",
              width: "min(100%, calc((100vh - 250px) * 16 / 9))",
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

            {/* 公共牌:桌心 5 个固定槽位。翻牌占 0–2 不再挪动,转牌/河牌依次补上;按上面的节奏一街一街亮。 */}
            {tableCardsVisible && (
              <div className="absolute left-1/2 top-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div
                    key={i}
                    className="relative h-20 w-14 shrink-0 rounded-[10px] border border-white/10 bg-black/15"
                    style={{ zIndex: 5 - i }}
                  >
                    {communityCards[i] && i < shownBoard ? (
                      // 翻牌:三张一起落在第一张的位置,再向右滑开到各自的槽位;转牌/河牌单张落下
                      <PokerCard
                        card={communityCards[i]}
                        size="md"
                        className={cn("h-full w-full", i < 3 ? "flop-slide" : "deal-in")}
                        style={i < 3 ? ({ "--dx": `${-i * 64}px` } as React.CSSProperties) : undefined}
                      />
                    ) : null}
                  </div>
                ))}
              </div>
            )}

            {/*
              底池:放在公共牌上方、桌子正中,筹码堆 + 数额。只渲染服务器给的数,不本地累加
              (累加就会和服务器分叉,见 docs/architecture.md 不变量 1)。
            */}
            {gameStarted && (
              <div
                className="absolute z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
                style={{ left: `${POT_SPOT.left}%`, top: `${POT_SPOT.top}%` }}
              >
                {pot > 0 && <ChipStack amount={pot} label={false} maxCols={6} />}
                <div className="rounded-full border border-amber-500/40 bg-black/70 px-2 py-px text-[11px] font-semibold leading-[18px] text-amber-200 shadow">
                  Pot <span data-testid="pot-amount" className="num text-xs">{formatChips(pot)}</span>
                </div>
              </div>
            )}

            {/*
              结算展示期的标签,占底池的位置。没有它,留在桌上的牌与「正在打的一手」在视觉上分不开。
            */}
            {showingShowdown && (
              <div
                className="absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded-full border border-emerald-500/40 bg-black/70 px-3 py-1 text-xs font-semibold text-emerald-200"
                style={{ left: `${POT_SPOT.left}%`, top: `${POT_SPOT.top}%` }}
                data-testid="showdown-recap"
              >
                上一手摊牌
              </div>
            )}

            {/* 本街各家的下注:从座位滑到身前的下注线上,筹码堆的高矮/颜色随金额变 */}
            {gameStarted &&
              state.players
                .filter((p) => p.bet_amount > 0)
                .map((p) => (
                  <FlyingChips
                    key={`bet-${state.handStatus}-${p.nickname}`}
                    from={spotOf(p.seat_position + 1)}
                    to={betSpotOf(p.seat_position + 1)}
                    amount={p.bet_amount}
                    tag={blindTag(p)}
                  />
                ))}

            {/* 换街收进底池 / 结算派给赢家 */}
            {flights.map((f) => (
              <FlyingChips key={f.id} from={f.from} to={f.to} amount={f.amount} label={false} delay={f.delay} duration={550} fadeOut />
            ))}

            {/* Player info cards - MANUAL POSITIONING */}
            {/* Each card position is set manually using percentages (0-100%) */}
            {/* left: 0% = far left, 50% = center, 100% = far right */}
            {/* top: 0% = top, 50% = center, 100% = bottom */}
            {seats.map((seat) => {
              // Seat positions: bottom row (5,6,7) aligned at same top; user (6) centered
              // 座位点位与大厅预览共用一份(见 components/tableSeats.ts),对准底图上画的 9 把椅子。
              // 画在哪把椅子上:按视角旋转过(自己永远在正下方),见上面的 spotOf。
              const position = spotOf(seat.number)

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

              // 只有自己的座位(以及摊牌后被亮牌的人)取得到牌,别人一律取不到——这正是隐私边界。
              // 所以必须兜 []:原 mock 给所有座位都发了牌,直接读 .length 不会崩,换成真实数据后
              // 其余座位是 undefined,页面会整个白屏(0080 由浏览器测试发现)。
              const isCurrent = seat.player.id === "current"
              // 这个座位上的人被摊牌亮过牌没有。只用在**别人**的座位上:自己的牌本来就朝上,
              // 该不该扣着由「我弃没弃牌」决定,与摊牌无关(自己也在 reveals 里,但那不是判据)。
              // 摊牌亮出的牌也等公共牌亮完再翻
              const revealed = boardDone ? revealedHands.get(seat.player.name) : undefined
              const isRevealed = revealed !== undefined
              // 自己的座位取自己的底牌,别人的座位只可能取到摊牌亮出来的那份。
              const hand = (tableCardsVisible ? (isCurrent ? myHand : revealed) : null) ?? []
              const lastAction = state.lastActions[seat.player.name]
              const seatFolded =
                tableCardsVisible && state.players.find((p) => p.nickname === seat.player!.name)?.status === "folded"

              return (
                <div
                  key={seat.number}
                  // 测试钩子:座位号 + 该座位当前筹码(服务器给的值)。按文案定位太脆——改一句提示
                  // 就能弄坏用例,而「这个座位上还剩多少」是免盲/买入/结算都要断言的东西。
                  data-seat={seat.number}
                  data-seat-points={seat.player.points ?? ""}
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
                  {/* 这一街最近的动作:Check / Call / Raise / Fold,像真牌桌那样挂在座位旁 */}
                  {/* 弃牌的人不挂气泡:变灰的信息条已经说明了 */}
                  {gameStarted && lastAction && lastAction.kind !== "fold" && (
                    <span
                      key={`${lastAction.kind}-${lastAction.amount}`}
                      data-action-bubble={lastAction.kind}
                      className={cn(
                        "deal-in absolute left-full top-1/2 z-30 ml-1 -translate-y-1/2 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-bold shadow-lg",
                        actionBubble(lastAction).cls,
                      )}
                    >
                      {actionBubble(lastAction).text}
                    </span>
                  )}
                  {isCurrent ? (
                    <>
                      {/* Current player: cards on top, half covered by info bar. Folded: no cards at all, just
                          the grey bar (the slot keeps its size so the bar doesn't jump). */}
                      {tableCardsVisible && hand.length === 2 && (
                        <div className={cn("relative z-0 flex justify-center -mb-10", currentPlayerFolded && "invisible")}>
                          <div className="flex items-end min-h-[32px]">
                            <PokerCard card={hand[0]} size="lg" className="origin-bottom -mr-6 -rotate-[10deg]" />
                            <PokerCard card={hand[1]} size="lg" className="origin-bottom -ml-6 rotate-[10deg]" />
                          </div>
                        </div>
                      )}
                      <div className="relative z-10">
                        {/* 「Folded」只在有牌可谈的时候才成立:进行中的手牌,或结算展示期。
                            state.players 不随 hand_ended 清空,所以不加闸门的话,弃过牌的人会在
                            两手之间一直挂着这个标(以及下面那层变暗),而那时他并不在任何一手里。 */}
                        <PlayerInfoCard
                          player={seat.player}
                          isButton={seat.isButton}
                          isCurrentPlayer={true}
                          folded={tableCardsVisible && currentPlayerFolded}
                          position={positions.get(seat.number - 1)}
                          className="scale-105"
                        />
                      </div>
                    </>
                  ) : (
                    <>
                      <PlayerInfoCard
                        player={seat.player}
                        isButton={seat.isButton}
                        isCurrentPlayer={false}
                        folded={seatFolded}
                        position={positions.get(seat.number - 1)}
                        className="scale-90"
                      />
                      {tableCardsVisible && !seatFolded && hand.length === 2 && (
                        // 摊牌亮出来的人才朝上。这里曾硬写 isHidden,所以亮牌事件收到了、也投影进来了,
                        // 渲染出来还是牌背 —— 而 BACKEND_GUIDE §5 的口径是「摊牌才翻」。
                        // `isHidden` 今天恒为 false(取不到牌就根本不渲染),写成条件式是因为判据在这里:
                        // 朝上与否取决于**服务器发没发这张牌**。局中给对手画牌背还没做,见 TODO 0105·A。
                        <div className="flex origin-center -mr-2">
                          <PokerCard card={hand[0]} isHidden={!isRevealed} size="lg" className="-mr-2" />
                          <PokerCard card={hand[1]} isHidden={!isRevealed} size="lg" className="-ml-2" />
                        </div>
                      )}
                    </>
                  )}
                </div>
              )
            })}

          </div>

          {/*
            ── 牌桌下方的操作区:行动按钮居中(正对自己的座位);入座/买入/准备/自动下一手放在左边。
            没有行动区的时候(两手之间、弃了牌)这些控件本身居中。都不压在桌面上。 ──
          */}
          <div
            className={cn(
              "w-full max-w-[1300px] gap-3",
              showActions ? "flex flex-col items-center lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-end" : "flex justify-center",
            )}
          >
            <div className={cn("flex flex-wrap items-center gap-2", showActions && "lg:justify-self-start")}>
              {!me && (
                <p className="rounded-lg border border-line bg-panel/90 px-3 py-2 text-sm text-muted-ink">
                  观战中 · 点桌上空位入座
                </p>
              )}

              {/* 已入座但桌上没筹码:必须先买入才能准备(buy_in)。 */}
              {me && me.points === 0 && (
                <div className="panel flex items-center gap-2 p-2">
                  <input
                    type="number"
                    aria-label="买入额"
                    value={buyInAmount || state.buyIn}
                    onChange={(e) => setBuyInAmount(Number(e.target.value))}
                    className="num h-10 w-28 rounded-lg border border-line bg-room px-3 text-lg font-semibold text-ink focus:border-gold focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={handleBuyIn}
                    className="h-10 rounded-lg bg-gold px-5 font-condensed text-lg font-semibold text-[#1a1405] hover:brightness-110"
                  >
                    买入
                  </button>
                </div>
              )}

              {/* 自动下一手:倒计时 + 开关 */}
              {me && (
                <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-panel/80 px-2.5 py-1.5 text-xs text-muted-foreground">
                  <input type="checkbox" checked={autoNext} onChange={(e) => toggleAutoNext(e.target.checked)} className="accent-amber-500" />
                  自动下一手
                </label>
              )}
              {handOver && autoNext && countdown !== null && (
                <span className="rounded-lg border border-amber-500/40 bg-black/50 px-3 py-1.5 text-sm font-semibold text-amber-200">
                  {countdown > 0 ? `下一手 ${countdown} 秒后开始` : "等待其他玩家准备…"}
                </span>
              )}

              {me && !gameStarted && (
                <>
                  {!isReady ? (
                    <button
                      type="button"
                      onClick={() => setReady(true, me.seat_position)}
                      disabled={me.points === 0}
                      className="h-12 rounded-xl bg-call px-8 font-condensed text-xl font-semibold text-white shadow-[0_10px_20px_-10px_rgba(0,0,0,0.9)] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-35"
                    >
                      Ready
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setReady(false, me.seat_position)}
                      title="取消准备"
                      className="h-12 rounded-xl border border-call/60 bg-call/15 px-5 font-condensed text-lg font-semibold text-call"
                    >
                      ✓ 已准备
                    </button>
                  )}
                  {isReady && readyPlayers < 2 && (
                    <span className="text-sm text-muted-foreground">
                      至少要 2 个准备好的玩家才能开局{process.env.NODE_ENV === "development" ? "。点右上角「AI 对手」加几个?" : "。"}
                    </span>
                  )}
                  {allReady && (
                    <button
                      type="button"
                      onClick={() => startHand(me.seat_position)}
                      className="h-12 rounded-xl bg-gold px-8 font-condensed text-xl font-semibold text-[#1a1405] shadow-[0_10px_20px_-10px_rgba(0,0,0,0.9)] hover:brightness-110"
                    >
                      Start Game
                    </button>
                  )}
                </>
              )}
            </div>

            {/* 自己在这手里且没弃牌才给行动区;弃了牌就收起来。 */}
            {showActions && mine && (
              <ActionPanel
                myTurn={canAct}
                turnKey={turnKey}
                actingName={acting?.nickname ?? null}
                callAmount={callAmount}
                minRaiseTo={state.minRaiseTo}
                pot={state.pot}
                lastBet={state.lastBet}
                stack={mine.points}
                myBet={mine.bet_amount}
                bigBlind={state.bigBlind}
                raiseLevel={state.betLevel + 1}
                othersCanAct={state.players.some((p) => p.nickname !== state.me && p.status === "active")}
                onFold={ifMyTurn(fold)}
                onCheck={ifMyTurn(check)}
                // 筹码不够跟满:跟注就是全下(目标额 = 自己能到的最高额),不然服务器会拒「超过可用」
                onCall={ifMyTurn(() => bet(Math.min(state.lastBet, mine.points + mine.bet_amount)))}
                // 留空就按服务器给的下限 min_raise_to,不用前端自编的式子(BUG-19)。
                onRaise={(to) => canAct && mine && bet(to || state.minRaiseTo)}
                onAllIn={ifMyTurn(() => bet(mine.points + mine.bet_amount))}
              />
            )}
            {/* grid 右栏占位,让中间一栏真正居中 */}
            {showActions && <div className="hidden lg:block" />}
          </div>
        </div>
      </div>

      <ConnectionBanner />
      <FreeEntryVote />
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
