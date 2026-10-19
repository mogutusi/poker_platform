"use client"

// Chip graphics for the table: a stack whose make-up follows the amount (bigger bets = more and
// higher-value chips, so sizing reads at a glance), and a wrapper that slides a stack from one
// point on the table to another (seat → bet line, bet line → pot, pot → winner).
//
// Positions are % of the table box. Purely visual: amounts come from the server, nothing here is
// counted or summed.

import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

// The chip set we play 1/2 with (100bb max buy-in = 200): highest first,
// [value, face colour, edge-stripe colour]. Same colours as a standard casino set.
const DENOMS: [number, string, string][] = [
  [100, "#222222", "#f2f2f2"],
  [25, "#1f8a4c", "#eafff2"],
  [5, "#c62828", "#ffffff"],
  [1, "#f2f2f2", "#2b6cb0"],
]

type Column = { key: string; face: string; edge: string; count: number }

/**
 * Break an amount into real chips (greedy over 100/25/5/1), one column per denomination; a
 * denomination with more than `perCol` chips continues in another column. Capped at `maxCols`
 * columns so a huge pot still fits. This is the actual chip count, not a symbolic stack.
 */
function breakdown(amount: number, perCol: number, maxCols: number, stackStyle: boolean): Column[] {
  const cols: Column[] = []
  let rest = Math.max(0, Math.round(amount))
  for (const [value, face, edge] of DENOMS) {
    // A player's stack at 1/2 is mostly greens and reds, like at a real table: about 200 always stays
    // in small chips and only the rest goes into black 100s (a 200 buy-in shows as 8 greens, not 2 blacks).
    let n = stackStyle && value === 100 ? Math.max(0, Math.floor((rest - 200) / 100)) : Math.floor(rest / value)
    rest -= n * value
    let i = 0
    while (n > 0) {
      const c = Math.min(perCol, n)
      cols.push({ key: `${value}-${i++}`, face, edge, count: c })
      n -= c
    }
  }
  return cols.slice(0, maxCols)
}

const SIZES = {
  xs: { px: 11, overlap: 8 },
  sm: { px: 14, overlap: 10 },
}

function Chip({ face, edge, px, style }: { face: string; edge: string; px: number; style?: React.CSSProperties }) {
  return (
    <span
      className="block rounded-full"
      style={{
        width: px,
        height: px,
        background: `radial-gradient(circle, ${face} 0 38%, transparent 39%), repeating-conic-gradient(${face} 0 30deg, ${edge} 30deg 45deg)`,
        boxShadow: `0 0 0 1px rgba(0,0,0,0.45), 0 2px 2px rgba(0,0,0,0.5)`,
        ...style,
      }}
    />
  )
}

export function ChipStack({
  amount,
  label = true,
  size = "xs",
  perCol = 8,
  maxCols = 4,
  stackStyle = false,
  tag,
  className,
}: {
  amount: number
  label?: boolean
  size?: keyof typeof SIZES
  perCol?: number
  maxCols?: number
  /** Break down like a player's stack (few 100s) rather than the fewest chips. */
  stackStyle?: boolean
  /** Marks a forced bet (the blinds): a coloured "SB" / "BB" prefix on the amount label. */
  tag?: "SB" | "BB"
  className?: string
}) {
  const { px, overlap } = SIZES[size]
  const cols = breakdown(amount, perCol, maxCols, stackStyle)
  return (
    <div className={cn("flex items-end gap-1", className)}>
      <div className="flex items-end gap-[2px]">
        {cols.map((c) => (
          <div key={c.key} className="flex flex-col-reverse items-center">
            {Array.from({ length: c.count }, (_, i) => (
              <Chip key={i} face={c.face} edge={c.edge} px={px} style={{ marginTop: i === 0 ? 0 : -overlap }} />
            ))}
          </div>
        ))}
      </div>
      {label && (
        <span
          data-blind={tag}
          className={cn(
            "num flex items-center gap-1 rounded-full px-1.5 text-[11px] font-semibold leading-[16px] ring-1",
            tag === "SB"
              ? "bg-sky-900/85 text-sky-100 ring-sky-400/70"
              : tag === "BB"
                ? "bg-violet-900/85 text-violet-100 ring-violet-400/70"
                : "bg-black/70 text-amber-200 ring-white/10",
          )}
        >
          {tag && <span className="font-extrabold tracking-wide">{tag}</span>}
          {amount.toLocaleString()}
        </span>
      )}
    </div>
  )
}

interface Point {
  left: number
  top: number
}

/**
 * A chip stack that appears at `from` and slides to `to`. Re-keyed by the caller to replay.
 * `fadeOut` makes it vanish on arrival (chips swept into the pot / handed to a winner).
 */
export function FlyingChips({
  from,
  to,
  amount,
  label = true,
  delay = 0,
  duration = 450,
  fadeOut = false,
  tag,
}: {
  from: Point
  to: Point
  amount: number
  label?: boolean
  delay?: number
  duration?: number
  fadeOut?: boolean
  tag?: "SB" | "BB"
}) {
  const [arrived, setArrived] = useState(false)
  useEffect(() => {
    // Two frames so the browser paints the start position before the transition kicks in.
    let r2 = 0
    const r1 = requestAnimationFrame(() => {
      r2 = requestAnimationFrame(() => setArrived(true))
    })
    return () => {
      cancelAnimationFrame(r1)
      cancelAnimationFrame(r2)
    }
  }, [])
  const at = arrived ? to : from
  return (
    <div
      className="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-1/2 motion-reduce:transition-none"
      style={{
        left: `${at.left}%`,
        top: `${at.top}%`,
        opacity: arrived && fadeOut ? 0 : 1,
        transition: `left ${duration}ms cubic-bezier(0.3,0.7,0.2,1) ${delay}ms, top ${duration}ms cubic-bezier(0.3,0.7,0.2,1) ${delay}ms, opacity 200ms ease ${delay + duration - 120}ms`,
      }}
    >
      <ChipStack amount={amount} label={label} tag={tag} />
    </div>
  )
}
