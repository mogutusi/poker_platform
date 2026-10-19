// Position names for the players dealt into a hand: BTN, SB, BB, UTG … CO.
//
// Display only. Inputs are the server's facts (who was dealt in, where the button is); the names
// follow the usual online-poker convention for that many players. Seats go clockwise in seat order.

/** Names between the big blind and the button, by how many seats there are (UTG first, CO last). */
const MIDDLE: Record<number, string[]> = {
  0: [],
  1: ["UTG"],
  2: ["UTG", "CO"],
  3: ["UTG", "HJ", "CO"],
  4: ["UTG", "LJ", "HJ", "CO"],
  5: ["UTG", "UTG+1", "LJ", "HJ", "CO"],
  6: ["UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"],
}

/**
 * Map seat_position → position name.
 * @param inHand seat positions of the players dealt into this hand
 * @param button the server's button_position (a seat position)
 * @param maxSeats seats at the table, for wrapping around
 */
export function positionNames(inHand: number[], button: number, maxSeats: number): Map<number, string> {
  const out = new Map<number, string>()
  const n = inHand.length
  if (n < 2) return out
  // Clockwise starting at the button.
  const order = [...inHand].sort((a, b) => ((a - button + maxSeats) % maxSeats) - ((b - button + maxSeats) % maxSeats))
  if (n === 2) {
    // Heads-up: the button posts the small blind.
    out.set(order[0], "BTN/SB")
    out.set(order[1], "BB")
    return out
  }
  out.set(order[0], "BTN")
  out.set(order[1], "SB")
  out.set(order[2], "BB")
  const middle = MIDDLE[n - 3] ?? MIDDLE[6]
  order.slice(3).forEach((seat, i) => out.set(seat, middle[i] ?? `MP${i}`))
  return out
}
