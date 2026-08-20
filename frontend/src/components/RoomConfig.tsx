'use client'

// 房间参数(小盲 / 买入额)。规则见 service/docs/core.md「SetSmallBlind / SetBuyIn」。
//
// 三条要在界面上表达清楚:
//   - **任何在房成员都能改**,没有房主。所以不做权限判断、不藏起来。
//   - **只能在两手之间改**,局中后端回 HAND_IN_PROGRESS。这是正确性校验不是权限问题,
//     所以界面提前禁用并说明原因,别让人点了才被拒。
//   - **大盲不单独设**,由 2×小盲派生——只给一个小盲输入框,并把算出来的大盲显示出来。

import { useState } from 'react'
import { setBuyIn, setSmallBlind } from '@/store/actions'
import { useRoom } from '@/store/useRoom'

export default function RoomConfig() {
  const state = useRoom()
  const [open, setOpen] = useState(false)
  const [sb, setSb] = useState('')
  const [bi, setBi] = useState('')

  // 手牌进行中不能改。提前禁用,并说明是「局中」而不是「你没权限」。
  const inHand = state.handStatus !== null
  const inRoom = state.room !== null
  if (!inRoom) return null

  const submitSb = () => {
    const n = Number(sb)
    if (Number.isFinite(n) && n > 0) {
      setSmallBlind(n)
      setSb('')
    }
  }
  const submitBi = () => {
    const n = Number(bi)
    if (Number.isFinite(n) && n > 0) {
      setBuyIn(n)
      setBi('')
    }
  }

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded border border-primary/40 px-2 py-1 text-primary/90 hover:bg-primary/10"
      >
        房间设置
      </button>

      {open && (
        <div className="absolute right-4 top-20 z-40 w-64 rounded-xl border-2 border-primary/40 bg-black/90 p-4 shadow-2xl backdrop-blur-sm">
          <div className="mb-3 flex items-center justify-between">
            <p className="uppercase tracking-[0.2em] text-muted-foreground">房间设置</p>
            <button
              type="button"
              aria-label="关闭房间设置"
              onClick={() => setOpen(false)}
              className="px-1 text-base leading-none text-muted-foreground hover:text-primary"
            >
              ×
            </button>
          </div>

          <p className="mb-3 text-muted-foreground">
            当前:小盲 {state.smallBlind} / 大盲 {state.bigBlind} / 买入 {state.buyIn}
          </p>

          {inHand ? (
            <p className="rounded bg-amber-500/15 px-3 py-2 text-amber-200">
              手牌进行中,等这手打完才能改。
            </p>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="text-muted-foreground" htmlFor="cfg-sb">
                  小盲(大盲自动 = 2×)
                </label>
                <div className="mt-1 flex gap-2">
                  <input
                    id="cfg-sb"
                    type="number"
                    min={1}
                    value={sb}
                    onChange={(e) => setSb(e.target.value)}
                    placeholder={String(state.smallBlind)}
                    className="h-8 flex-1 rounded border border-primary/40 bg-black/30 px-2"
                  />
                  <button
                    type="button"
                    onClick={submitSb}
                    className="rounded bg-primary px-3 font-semibold text-primary-foreground hover:bg-primary/90"
                  >
                    改
                  </button>
                </div>
                {sb !== '' && Number(sb) > 0 && (
                  <p className="mt-1 text-muted-foreground">大盲将变成 {Number(sb) * 2}</p>
                )}
              </div>

              <div>
                <label className="text-muted-foreground" htmlFor="cfg-bi">
                  默认买入额
                </label>
                <div className="mt-1 flex gap-2">
                  <input
                    id="cfg-bi"
                    type="number"
                    min={1}
                    value={bi}
                    onChange={(e) => setBi(e.target.value)}
                    placeholder={String(state.buyIn)}
                    className="h-8 flex-1 rounded border border-primary/40 bg-black/30 px-2"
                  />
                  <button
                    type="button"
                    onClick={submitBi}
                    className="rounded bg-primary px-3 font-semibold text-primary-foreground hover:bg-primary/90"
                  >
                    改
                  </button>
                </div>
              </div>

              <p className="text-muted-foreground">房里任何人都能改,没有房主。</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
