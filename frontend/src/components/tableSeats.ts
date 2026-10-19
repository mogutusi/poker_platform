// Seat spots on the table photo (game-table.jpg), shared by the table page and the lobby preview
// so a player sitting at seat N shows up on the same side of the table in both places.
//
// Values are % of the table page's 16:9 box, measured onto the 9 chairs painted in the photo.
// The 3:2 photo is object-contain'ed in that box, so it only spans x ≈ 7.8%–92.2%: each spot is the
// chair centre (fraction of the photo) mapped through that band. Seat numbers are 1-based
// (= server seat_position + 1). Rooms are created with DEV_SEATS=9 (service/app/poker.env.example).
export const TABLE_SEAT_SPOTS: Record<number, { left: number; top: number }> = {
  1: { left: 30, top: 12 },
  2: { left: 50, top: 10 },
  3: { left: 70, top: 12 },
  4: { left: 87, top: 42 },
  5: { left: 72.5, top: 82 },
  6: { left: 50.5, top: 85 },
  7: { left: 27, top: 82 },
  8: { left: 14.5, top: 60 },
  9: { left: 17, top: 27 },
}

/**
 * Direction of seat N from the table centre, in radians (screen coordinates, y down), measured on
 * the 16:9 table page. The lobby's oval preview places its seats along these same directions.
 */
export function seatAngle(seatNumber: number): number {
  const spot = TABLE_SEAT_SPOTS[seatNumber]
  if (!spot) return -Math.PI / 2
  return Math.atan2(spot.top - 50, ((spot.left - 50) * 16) / 9)
}
