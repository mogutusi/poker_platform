"use client"

// A small, readable playing card for lists (hand history): rank over suit, drawn with text instead
// of the full card picture, which turns to mush when scaled down this far. Takes a treys code
// ("As", "Td", "7c"). Four-colour deck so suits read at a glance: ♠ black, ♥ red, ♦ blue, ♣ green.

import { cn } from "@/lib/utils"

const SUIT: Record<string, { sym: string; color: string }> = {
  s: { sym: "♠", color: "text-slate-900" },
  h: { sym: "♥", color: "text-red-600" },
  d: { sym: "♦", color: "text-blue-600" },
  c: { sym: "♣", color: "text-emerald-700" },
}

const SIZES = {
  sm: { box: "h-[30px] w-[22px]", rank: "text-[12px]", ten: "text-[10px]", suit: "text-[11px]" },
  xs: { box: "h-[22px] w-[16px] rounded-[3px]", rank: "text-[10px]", ten: "text-[8px]", suit: "text-[8px]" },
}

export default function MiniCard({
  code,
  hidden = false,
  size = "sm",
  className,
}: {
  code?: string
  hidden?: boolean
  size?: keyof typeof SIZES
  className?: string
}) {
  const z = SIZES[size]
  if (hidden || !code) {
    return (
      <span
        aria-label="底牌未公开"
        className={cn(
          "inline-block shrink-0 rounded-[4px] border border-white/40 shadow-sm",
          z.box,
          "bg-[repeating-linear-gradient(45deg,#7f1d1d_0_3px,#b91c1c_3px_6px)]",
          className,
        )}
      />
    )
  }
  const rank = code[0] === "T" ? "10" : code[0]
  const suit = SUIT[code[1]] ?? SUIT.s
  return (
    <span
      data-card={code}
      aria-label={code}
      className={cn(
        "inline-flex shrink-0 flex-col items-center justify-center rounded-[4px] border border-black/10 bg-white leading-none shadow-sm",
        z.box,
        suit.color,
        className,
      )}
    >
      <span className={cn("font-extrabold", rank === "10" ? cn(z.ten, "tracking-[-0.06em]") : z.rank)}>{rank}</span>
      <span className={z.suit}>{suit.sym}</span>
    </span>
  )
}
