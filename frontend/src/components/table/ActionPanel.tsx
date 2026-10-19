"use client"

// The bottom-right action panel: bet sizing on top, the three big decisions below.
//
// Legality stays with the server (docs/architecture.md, frontend invariant 1): the server still
// judges every action and answers with an error pill if it disagrees. What this panel does is grey
// out the choices that obviously aren't there, from the numbers the server already sent:
//   - nothing to call → no Fold / Call (Fold would be rejected: check is free)
//   - something to call → no Check
//   - can't make a full raise → no Raise / sizing (an all-in for more is still allowed)
//   - nobody else left who can act (everyone else all-in) → only Call or Fold
// The sizing presets are only *suggestions* for the amount box; the floor is the server's
// min_raise_to and nothing here re-derives it.

import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"
import { ALLIN_CLASS, levelClass, levelName } from "./betLevel"

interface ActionPanelProps {
  myTurn: boolean
  /** Changes when a new turn starts; resets the amount box to the server's floor. */
  turnKey: string
  actingName: string | null
  callAmount: number
  minRaiseTo: number
  pot: number
  lastBet: number
  /** Your remaining stack and what you've already put in this street. */
  stack: number
  myBet: number
  bigBlind: number
  /** What a raise now would make it on this street (2 = raise, 3 = 3-bet, …); colours the button. */
  raiseLevel: number
  /** Someone besides you can still act (not folded, not all-in). If not, raising is pointless. */
  othersCanAct: boolean
  onFold: () => void
  onCheck: () => void
  onCall: () => void
  onRaise: (to: number) => void
  onAllIn: () => void
}

export default function ActionPanel(p: ActionPanelProps) {
  // null = follow the server's floor (min_raise_to) as it changes.
  const [amount, setAmount] = useState<number | null>(null)
  useEffect(() => setAmount(null), [p.turnKey])

  const maxTo = p.stack + p.myBet
  const floor = Math.min(p.minRaiseTo, maxTo)
  const raiseTo = amount ?? p.minRaiseTo
  const clamp = (v: number) => Math.max(floor, Math.min(maxTo, Math.round(v)))

  // Pot-fraction raise-to: call first, then raise by a fraction of the pot after calling.
  const potAfterCall = p.pot + p.callAmount
  const presets: { label: string; to: number }[] = [
    { label: "Min", to: p.minRaiseTo },
    { label: "½ Pot", to: clamp(p.lastBet + potAfterCall / 2) },
    { label: "¾ Pot", to: clamp(p.lastBet + (potAfterCall * 3) / 4) },
    { label: "Pot", to: clamp(p.lastBet + potAfterCall) },
  ]

  const disabled = !p.myTurn
  const facing = p.callAmount > 0
  const callFor = Math.min(p.callAmount, p.stack) // short stack: calling puts you all-in for less
  const canCheck = !facing
  const canFold = facing
  const canCall = facing && p.stack > 0
  const canRaise = p.othersCanAct && maxTo >= p.minRaiseTo // a full raise fits in your stack
  const canShove = p.othersCanAct && maxTo > p.lastBet // all-in puts in more than a call
  const bigBtn =
    "h-14 flex-1 rounded-xl font-condensed text-xl font-semibold tracking-wide text-white shadow-[0_10px_20px_-10px_rgba(0,0,0,0.9)] transition-[filter,transform] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-35 enabled:hover:brightness-110"
  /** On your turn, a choice that isn't available is solid grey (not just dimmed like when it's not your turn). */
  const off = (available: boolean) =>
    p.myTurn && !available ? "bg-none! bg-slate-700! text-slate-400! ring-0! shadow-none! opacity-60!" : ""

  return (
    // One row under the table: sizing on the left, the three decisions on the right.
    <div className="flex w-[780px] max-w-full flex-col gap-1.5">
      {/* Who's on the clock. When it isn't you the buttons are dim, so say who we're waiting for. */}
      <p className={cn("text-center text-sm", p.myTurn ? "font-semibold text-gold" : "text-muted-ink")}>
        {p.myTurn ? "轮到你行动" : `等待 ${p.actingName ?? "…"} 行动`}
      </p>

      <div className="flex items-stretch gap-2">
      <div className="panel flex min-w-0 flex-1 flex-col gap-2 p-2.5">
        <div className="flex items-center gap-1.5">
          {presets.map((pr) => (
            <button
              key={pr.label}
              type="button"
              disabled={disabled || !canRaise}
              onClick={() => setAmount(pr.to)}
              className={cn(
                "h-8 flex-1 rounded-md border font-condensed text-sm font-semibold transition-colors disabled:opacity-35",
                raiseTo === pr.to && canRaise
                  ? "border-raise/70 bg-raise/15 text-raise"
                  : "border-line bg-panel-raised text-ink enabled:hover:border-raise/50",
              )}
            >
              {pr.label}
            </button>
          ))}
          <input
            type="number"
            aria-label="加注到"
            min={p.minRaiseTo}
            value={raiseTo}
            disabled={disabled || !canRaise}
            onChange={(e) => setAmount(Number(e.target.value) || raiseTo)}
            className="num h-8 w-20 rounded-md border border-line bg-room px-2 text-right text-base font-semibold text-ink focus:border-raise focus:outline-none disabled:opacity-35"
          />
        </div>
        <input
          type="range"
          aria-label="加注滑杆"
          min={floor}
          max={Math.max(floor, maxTo)}
          step={Math.max(1, p.bigBlind / 2)}
          value={Math.min(Math.max(raiseTo, floor), maxTo)}
          disabled={disabled || !canRaise || maxTo <= floor}
          onChange={(e) => setAmount(Number(e.target.value))}
          className="h-2 w-full cursor-pointer accent-[#f09a2e] disabled:opacity-35"
        />
        <div className="flex gap-1.5">
          <button
            type="button"
            aria-label="Raise"
            disabled={disabled || !canRaise}
            onClick={() => p.onRaise(raiseTo)}
            className={cn(bigBtn, "h-10 text-base", levelClass(p.lastBet === 0 ? 1 : p.raiseLevel), off(canRaise))}
          >
            {p.lastBet === 0 ? "Bet" : `${levelName(p.raiseLevel)} to`} <span className="num">{raiseTo.toLocaleString()}</span>
          </button>
          <button
            type="button"
            disabled={disabled || !canShove}
            onClick={p.onAllIn}
            className={cn(bigBtn, "h-10 flex-none px-4 text-base", ALLIN_CLASS, off(canShove))}
          >
            All-in
          </button>
        </div>
      </div>

      <div className="flex w-[370px] shrink-0 gap-2">
        <button type="button" disabled={disabled || !canFold} onClick={p.onFold} className={cn(bigBtn, "h-auto bg-fold", off(canFold))}>
          Fold
        </button>
        <button
          type="button"
          disabled={disabled || !canCheck}
          onClick={p.onCheck}
          className={cn(bigBtn, "h-auto bg-[oklch(0.4_0.05_50)]", off(canCheck))}
        >
          Check
        </button>
        <button
          type="button"
          disabled={disabled || !canCall}
          onClick={p.onCall}
          className={cn(bigBtn, "h-auto flex-col gap-0 bg-call leading-tight", off(canCall))}
        >
          <span>
            Call <span className="num">{callFor.toLocaleString()}</span>
          </span>
          {canCall && callFor < p.callAmount && <span className="text-[11px] font-bold tracking-wider text-white/85">ALL-IN</span>}
        </button>
      </div>
      </div>
    </div>
  )
}
