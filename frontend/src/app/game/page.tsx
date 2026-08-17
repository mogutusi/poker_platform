"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import PlayerInfoCard from "@/components/PlayerInfoCard"
import PokerCard from "@/components/PokerCard"
import Image from "next/image"
import gamingTableBg from "@/pics/game-table.jpg"
import { cn } from "@/lib/utils"
import { createDeck } from "@/utils/poker"
import type { Card as PokerCardType } from "@/types/poker"

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
function GameView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const seatNumber = parseInt(searchParams.get("seat") || "0")
  const [playerName, setPlayerName] = useState<string>("")
  const [points, setPoints] = useState<number>(1000)
  const [isReady, setIsReady] = useState<boolean>(false)

  // Mock seat data - in real app, this would come from API/WebSocket
  // Seat 6 will be assigned to current player in useEffect
  const [seats, setSeats] = useState<Seat[]>([
    { number: 1, player: { id: "1", name: "HighRoller", points: 3250, isReady: true } },
    { number: 2, player: { id: "2", name: "RiverKing", points: 2812, isReady: false } },
    { number: 3, player: { id: "3", name: "John Doe", points: 320, isReady: true } },
    { number: 4, player: { id: "4", name: "AllInQueen", points: 2344, isReady: true } },
    { number: 5, isButton: true, player: { id: "5", name: "SlowPlay", points: 1980, isReady: false } },
    { number: 6 }, // Will be assigned to current player
    { number: 7, player: { id: "7", name: "ChipLeader", points: 1765, isReady: false } },
    { number: 8, player: { id: "8", name: "Tom Dwan", points: 2344, isReady: true } },
    { number: 9, player: { id: "9", name: "Tan Xuan", points: 2344, isReady: true } },
  ])

  // Game mode: after all ready and Start Game
  const [gameStarted, setGameStarted] = useState(false)
  const [playerHands, setPlayerHands] = useState<Record<number, PokerCardType[]>>({})
  const [currentBet, setCurrentBet] = useState(0)
  const [callAmount, setCallAmount] = useState(20)
  const [raiseAmount, setRaiseAmount] = useState(40)
  const [pot, setPot] = useState(0)
  const [phase, setPhase] = useState<"preflop" | "flop" | "turn" | "river" | "showdown">("preflop")
  const [communityCards, setCommunityCards] = useState<PokerCardType[]>([])
  const [remainingDeck, setRemainingDeck] = useState<PokerCardType[]>([])
  const [foldedSeats, setFoldedSeats] = useState<Set<number>>(new Set())

  const currentPlayers = useMemo(
    () => seats.filter((seat) => seat.player).length,
    [seats]
  )

  const readyPlayers = useMemo(
    () => seats.filter((seat) => seat.player?.isReady).length,
    [seats]
  )

  const allReady = useMemo(
    () => currentPlayers > 0 && readyPlayers === currentPlayers,
    [currentPlayers, readyPlayers]
  )

  useEffect(() => {
    // Simple auth gate: if no token, back to login
    const token = typeof window !== "undefined" ? localStorage.getItem("auth_token") : null
    if (!token) {
      router.replace("/")
      return
    }

    // Use last login name from storage if available
    const storedName =
      (typeof window !== "undefined" && localStorage.getItem("player_name")) || "Player"
    setPlayerName(storedName)

    // Always assign current player to seat 6
    setSeats((prevSeats) =>
      prevSeats.map((s) =>
        s.number === 6
          ? { ...s, player: { id: "current", name: storedName, points: points, isReady: false } }
          : s
      )
    )

    // TODO(0077):这一页还没接后端。改由 ws 的 join_room -> StateSnapshot 驱动,
    // 并拆掉本文件里的本地 mock 发牌与街道推进(见 docs/state.md)。
  }, [router, points])

  const handleReady = async () => {
    try {
      setIsReady(true)
      setSeats((prevSeats) =>
        prevSeats.map((s) =>
          s.number === 6 && s.player
            ? { ...s, player: { ...s.player, isReady: true } }
            : s
        )
      )
    } catch (error) {
      console.error("Failed to set ready:", error)
    }
  }

  /** For testing: mark all players ready so "Start Game" appears */
  const handleSetAllReadyTest = () => {
    setIsReady(true)
    setSeats((prevSeats) =>
      prevSeats.map((s) =>
        s.player ? { ...s, player: { ...s.player, isReady: true } } : s
      )
    )
  }

  const handleStartGame = async () => {
    if (!allReady) return

    try {
      const deck = createDeck()
      const hands: Record<number, PokerCardType[]> = {}
      let idx = 0
      seats.forEach((seat) => {
        if (seat.player && idx + 2 <= deck.length) {
          hands[seat.number] = [deck[idx], deck[idx + 1]]
          idx += 2
        }
      })
      setPlayerHands(hands)
      setGameStarted(true)
      setCurrentBet(20)
      setCallAmount(20)
      setRaiseAmount(40)
      setPot(readyPlayers * 30)
      setPhase("preflop")
      setCommunityCards([])
      setRemainingDeck(deck.slice(idx))
      setFoldedSeats(new Set())
    } catch (error) {
      console.error("Failed to start game:", error)
    }
  }

  const advanceToFlop = () => {
    setRemainingDeck((prev) => {
      if (prev.length >= 3) {
        setCommunityCards(prev.slice(0, 3))
        setPhase("flop")
        return prev.slice(3)
      }
      return prev
    })
  }
  const advanceToTurn = () => {
    setRemainingDeck((prev) => {
      if (prev.length < 1) return prev
      const card = prev[0]
      setCommunityCards((c) => {
        if (c.length !== 3) return c
        setPhase("turn")
        return [...c, card]
      })
      return prev.slice(1)
    })
  }
  const advanceToRiver = () => {
    setRemainingDeck((prev) => {
      if (prev.length < 1) return prev
      const card = prev[0]
      setCommunityCards((c) => {
        if (c.length !== 4) return c
        setPhase("river")
        return [...c, card]
      })
      return prev.slice(1)
    })
  }
  const advanceToShowdown = () => {
    setPhase((p) => (p === "river" ? "showdown" : p))
  }

  const handleAction = (action: "fold" | "check" | "call" | "raise" | "all-in") => {
    if (action === "fold") {
      setFoldedSeats((prev) => new Set(prev).add(6)) // current player is seat 6
    }
    if (action === "raise") {
      setCurrentBet(raiseAmount)
    }
    if (action === "all-in") {
      setCurrentBet(points)
    }
  }

  const currentPlayerFolded = foldedSeats.has(6)

  const handleLeave = () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem("auth_token")
      localStorage.removeItem("player_name")
    }
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
            {/* Temporary street buttons – left of Ready */}
            {gameStarted && (
              <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-black/60 px-2 py-1.5">
                <span className="text-[10px] font-semibold uppercase text-amber-500/90 mr-0.5">Tmp:</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={advanceToFlop}
                  disabled={phase !== "preflop"}
                  className="h-6 rounded px-2 text-[11px] border-amber-500/50 text-amber-200 hover:bg-amber-500/20"
                >
                  Flop
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={advanceToTurn}
                  disabled={phase !== "flop"}
                  className="h-6 rounded px-2 text-[11px] border-amber-500/50 text-amber-200 hover:bg-amber-500/20"
                >
                  Turn
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={advanceToRiver}
                  disabled={phase !== "turn"}
                  className="h-6 rounded px-2 text-[11px] border-amber-500/50 text-amber-200 hover:bg-amber-500/20"
                >
                  River
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={advanceToShowdown}
                  disabled={phase !== "river"}
                  className="h-6 rounded px-2 text-[11px] border-amber-500/50 text-amber-200 hover:bg-amber-500/20"
                >
                  Showdown
                </Button>
              </div>
            )}
            {!isReady ? (
              <Button
                onClick={handleReady}
                className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold"
              >
                Ready
              </Button>
            ) : (
              <div className="px-4 py-2 bg-green-500/20 border border-green-500/50 rounded-lg">
                <span className="text-green-400 font-bold text-sm">✓ Ready</span>
              </div>
            )}
            {!allReady && !gameStarted && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleSetAllReadyTest}
                className="border-amber-500/50 text-amber-600 dark:text-amber-400 text-xs"
              >
                Test: Set all ready
              </Button>
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
            <Button
              variant="outline"
              size="sm"
              onClick={handleLeave}
              className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
            >
              Leave
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
              if (!seat.player) return null

              // Seat positions: bottom row (5,6,7) aligned at same top; user (6) centered
              const manualPositions: Record<number, { left: number; top: number }> = {
                1: { left: 25, top: 11 },
                2: { left: 50, top: 9 },
                3: { left: 77, top: 12 },
                4: { left: 95, top: 40 },
                5: { left: 72, top: 84 },
                6: { left: 50, top: 84 },  // User – aligned with 5 & 7
                7: { left: 28, top: 84 },
                8: { left: 5, top: 60 },
                9: { left: 7, top: 30 },
              }

              const position = manualPositions[seat.number] || { left: 50, top: 50 }

              const hand = gameStarted ? playerHands[seat.number] : []
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
              <div className="flex items-center gap-1.5 rounded-full bg-black/70 backdrop-blur-sm border border-white/10 py-1.5 px-2 shadow-lg">
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => handleAction("fold")}
                  className="h-7 rounded-full px-3 text-xs font-semibold shadow-sm min-w-0"
                >
                  Fold
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => handleAction("check")}
                  className="h-7 rounded-full px-3 text-xs font-semibold border-white/10 min-w-0"
                >
                  Check
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleAction("call")}
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
                    className="h-7 rounded-full px-3 text-xs font-semibold bg-amber-600 hover:bg-amber-700 text-white min-w-0"
                  >
                    Raise
                  </Button>
                </div>
                <Button
                  size="sm"
                  onClick={() => handleAction("all-in")}
                  className="h-7 rounded-full px-3 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white min-w-0"
                >
                  All-in
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>
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
