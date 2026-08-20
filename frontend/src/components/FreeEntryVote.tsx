'use client'

// 免盲投票面板(规则见 service/docs/rules.md ①「免盲投票」)。
//
// 规则要点,界面必须表达清楚,否则没人知道自己在投什么:
//   - 免盲 = 已入局玩家全票同意,免掉新人**这一次**的入局盲,让他直接正常入局。
//   - 投票人只有「已入局且已准备」的座位——放人免费进来影响的是他们。新人、观战者、坐出者不投票。
//   - **全票才通过,任一反对立即失败**。所以「还差谁」比「已有几票」更值得显示。
//   - 候选在开票时冻结:开票后才坐下的新人不在这批里,通过也不免他。

import { voteFreeEntry } from '@/store/actions'
import { useRoom } from '@/store/useRoom'

export default function FreeEntryVote() {
  const state = useRoom()
  const vote = state.freeEntryVote
  if (!vote) return null

  const { candidates, voters, approvals } = vote
  const iAmVoter = state.me !== null && voters.includes(state.me)
  const iVoted = state.me !== null && approvals.includes(state.me)
  const pending = voters.filter((v) => !approvals.includes(v))

  return (
    <div
      role="dialog"
      aria-label="免盲投票"
      className="absolute right-4 top-20 z-40 w-72 rounded-xl border-2 border-amber-500/50 bg-black/90 p-4 shadow-2xl backdrop-blur-sm"
    >
      <p className="text-xs uppercase tracking-[0.2em] text-amber-400">免盲投票</p>

      <p className="mt-2 text-sm text-card-foreground">
        <span className="font-semibold text-primary">{candidates.join('、')}</span>
        {' '}想免掉这次的入局盲,直接入局。
      </p>
      <p className="mt-1 text-xs text-muted-foreground">全票同意才通过,任一人反对即失败。</p>

      {/* 全票制下「还差谁」比「几票」有用:知道在等谁,才知道要不要去催。 */}
      <div className="mt-3 text-xs">
        <p className="text-muted-foreground">
          已同意 {approvals.length} / {voters.length}
        </p>
        {pending.length > 0 && (
          <p className="mt-1 text-amber-200">等待:{pending.join('、')}</p>
        )}
      </div>

      {iAmVoter ? (
        iVoted ? (
          <p className="mt-3 rounded bg-emerald-500/15 px-3 py-2 text-center text-xs text-emerald-300">
            你已同意,等其他人
          </p>
        ) : (
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => voteFreeEntry(true)}
              className="flex-1 rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
            >
              同意
            </button>
            <button
              type="button"
              onClick={() => voteFreeEntry(false)}
              className="flex-1 rounded bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
            >
              反对
            </button>
          </div>
        )
      ) : (
        // 不是投票人也要看得到进展:被投的新人最关心结果,却没有投票权。
        <p className="mt-3 text-center text-xs text-muted-foreground">你不是本次的投票人</p>
      )}
    </div>
  )
}
