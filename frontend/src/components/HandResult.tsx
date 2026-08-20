'use client'

// 手牌结算结果。
//
// `hand_ended` 带着 winnings(谁赢了多少)和 refunds(退还的未叫注),store 一直收着,
// 但此前没接到界面上——打完一手用户只看到「In game」消失,不知道自己赢了还是输了。
//
// 退还和赢取要分开显示:退还是「没人跟的注还给你」,不是赢来的钱,混在一起会让人以为赢得更多。

import { useRoom } from '@/store/useRoom'
import type { NickAmount } from '@/types/wire.gen'

function Row({ entry, isMe }: { entry: NickAmount; isMe: boolean }) {
  return (
    <div className={['flex items-center justify-between gap-4 rounded px-2 py-1', isMe ? 'bg-primary/15' : ''].join(' ')}>
      <span className={isMe ? 'font-semibold text-primary' : 'text-card-foreground'}>
        {entry.nickname}
        {isMe && <span className="ml-1 text-xs text-muted-foreground">(你)</span>}
      </span>
      <span className="font-mono text-emerald-400">+{entry.amount}</span>
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
    <div
      role="status"
      className="absolute left-1/2 top-1/2 z-40 w-80 -translate-x-1/2 -translate-y-1/2 rounded-xl border-2 border-primary/50 bg-black/90 p-4 shadow-2xl backdrop-blur-sm"
    >
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm uppercase tracking-[0.2em] text-muted-foreground">本手结算</p>
        <button
          type="button"
          aria-label="关闭结算"
          onClick={onDismiss}
          className="rounded px-2 text-lg leading-none text-muted-foreground hover:text-primary"
        >
          ×
        </button>
      </div>

      {/* 先说和你有关的那句,再列全表——用户最想知道的是自己赢没赢。 */}
      <p className="mb-3 text-center text-lg font-bold">
        {myWin ? (
          <span className="text-emerald-400">你赢了 {myWin.amount}</span>
        ) : (
          <span className="text-muted-foreground">这手没有赢到底池</span>
        )}
      </p>

      <div className="space-y-1 text-sm">
        {winnings.map((w) => (
          <Row key={`w-${w.nickname}`} entry={w} isMe={w.nickname === state.me} />
        ))}
      </div>

      {refunds.length > 0 && (
        <>
          {/* 退还 ≠ 赢取:这是没人跟的注原样还给你的,分开列才不会被当成收益。 */}
          <p className="mb-1 mt-3 text-xs text-muted-foreground">退还(无人跟注的部分)</p>
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
