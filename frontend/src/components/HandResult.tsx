'use client'

// Hand result.
//
// `hand_ended` carries winnings (who won how much) and refunds (uncalled bets returned).
// Refunds are listed apart from winnings: a returned bet is not money won, and mixing them
// would make a win look bigger than it was.
//
// Placement: this panel sits in the bottom dock (left of the action area), never over the felt.
// The earlier version floated over the table and had to be hand-measured to miss the board
// (0105); in the dock it can't cover the showdown cards at any viewport size.

import { useRoom } from '@/store/useRoom'
import type { NickAmount } from '@/types/wire.gen'

function Row({ entry, isMe }: { entry: NickAmount; isMe: boolean }) {
  return (
    <div className={['flex items-center justify-between gap-4 rounded-md px-2 py-1', isMe ? 'bg-gold/10' : ''].join(' ')}>
      <span className={isMe ? 'font-semibold text-gold' : 'text-ink'}>
        {entry.nickname}
        {isMe && <span className="ml-1 text-xs text-muted-ink">(你)</span>}
      </span>
      <span className="num text-base font-semibold text-call">+{entry.amount.toLocaleString()}</span>
    </div>
  )
}

export default function HandResult({ onDismiss }: { onDismiss: () => void }) {
  const state = useRoom()
  const result = state.lastResult
  if (!result) return null

  const { winnings, refunds } = result
  const myWin = winnings.find((w) => w.nickname === state.me)

  return (
    <div role="status" className="panel deal-in w-72 p-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-semibold text-muted-ink">本手结算</p>
        <button
          type="button"
          aria-label="关闭结算"
          onClick={onDismiss}
          className="rounded px-2 text-lg leading-none text-muted-ink hover:text-ink"
        >
          ×
        </button>
      </div>

      {/* Lead with what the player wants to know: did I win? */}
      <p className="mb-3 font-condensed text-2xl font-semibold">
        {myWin ? (
          <span className="text-call">你赢了 {myWin.amount.toLocaleString()}</span>
        ) : (
          <span className="text-muted-ink">这手没有赢到底池</span>
        )}
      </p>

      <div className="space-y-1 text-sm">
        {winnings.map((w) => (
          <Row key={`w-${w.nickname}`} entry={w} isMe={w.nickname === state.me} />
        ))}
      </div>

      {refunds.length > 0 && (
        <>
          <p className="mb-1 mt-3 text-xs text-muted-ink">退还(无人跟注的部分)</p>
          <div className="space-y-1 text-sm">
            {refunds.map((r) => (
              <Row key={`r-${r.nickname}`} entry={r} isMe={r.nickname === state.me} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
