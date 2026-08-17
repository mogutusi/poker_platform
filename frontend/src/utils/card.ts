// 协议牌 → UI 牌的适配。
//
// 两套类型是有意分开的(见 docs/architecture.md):wire.gen.ts 是后端 codegen 的协议类型,
// poker.ts 是 UI 展示类型(牌面图按 10c.png / Ah.png 命名)。差异有两处:
//   花色  协议 h/d/c/s      ↔  UI hearts/diamonds/clubs/spades
//   点数  协议 T(ten)      ↔  UI 10(牌面图文件名用 10)
// 适配只在这一处发生,别的地方不许各自转换。

import type { Card as WireCard } from '@/types/wire.gen'
import type { Card as UiCard } from '@/types/poker'

/** 牌面数值,UI 类型要求带上;A 记 14(比大小只在后端做,这里纯为满足类型)。 */
const RANK_VALUE: Record<string, number> = {
  '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
  T: 10, J: 11, Q: 12, K: 13, A: 14,
}

const SUIT: Record<WireCard['suit'], UiCard['suit']> = {
  h: 'hearts',
  d: 'diamonds',
  c: 'clubs',
  s: 'spades',
}

export function toUiCard(card: WireCard): UiCard {
  return {
    suit: SUIT[card.suit],
    rank: (card.rank === 'T' ? '10' : card.rank) as UiCard['rank'],
    value: RANK_VALUE[card.rank] ?? 0,
  }
}

export function toUiCards(cards: readonly WireCard[]): UiCard[] {
  return cards.map(toUiCard)
}
