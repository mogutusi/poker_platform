// How aggressive the betting on this street has got, as a name and a colour that gets redder the
// further it goes: Bet → Raise → 3-Bet → 4-Bet → 5-Bet+ → All-in. Display only (the level comes
// from store/room.ts betLevel, which only counts what the server reported).

/** "Bet", "Raise", "3-Bet", "4-Bet", … for the bet that brings the street to `level`. */
export function levelName(level: number): string {
  if (level <= 1) return "Bet"
  if (level === 2) return "Raise"
  return `${level}-Bet`
}

/** Background/text classes for an action bubble or button at this level. */
export function levelClass(level: number): string {
  if (level <= 1) return "bg-amber-400 text-black"
  if (level === 2) return "bg-orange-500 text-white"
  if (level === 3) return "bg-red-500 text-white"
  if (level === 4) return "bg-red-700 text-white"
  return "bg-red-900 text-white ring-1 ring-red-400/70"
}

/** All-in is always the reddest thing on the table. */
export const ALLIN_CLASS =
  "bg-gradient-to-b from-red-700 to-[#45000a] text-white ring-2 ring-red-500/80 shadow-[0_0_14px_rgba(239,68,68,0.65)]"
