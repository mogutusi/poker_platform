// 界面上跟注打到摊牌:验多街推进、公共牌渲染、结算面板。协议层在 npm run smoke 里验过,
// 这里验的是「这些事在界面上真的看得见」(0080·A)。
//
// ⚠️ 这个用例在 0087 之前是**假绿的**:推进循环先点 Check 再点 Call,而按钮不按规则灰,
// 于是 heads-up preflop 里小盲那一下 Check 必被 ILLEGAL_ACTION 拒,循环从头到尾原地空转,
// 最后靠 ACTION_TIMEOUT(15 秒)替人默认弃牌收场——「In game 消失」照样成立,用例照样绿,
// 而它自称验过的三条街和摊牌一次都没走到。所以现在:推进用共用的 checkOrCall(认服务器的拒绝),
// 断言换成**只有真走到河牌才可能成立**的东西(正面朝上的牌 = 自己 2 张 + 公共 5 张)。
//
// 前置:后端在跑(见 docs/dev.md)。

import { expect, test } from '@playwright/test'
import { FACE_UP_CARD, advanceHand, joinTable } from './helpers'

/** 走到河牌时这一页上正面朝上的牌数:自己的底牌 2 + 公共牌 5(别人的底牌摊牌前结构性缺位)。 */
const FACE_UP_AT_RIVER = 7

/**
 * 摊牌之后这一页上正面朝上的牌数:再加对手亮出的 2 张 = 9。
 *
 * `reveals` 带的是**每个未弃牌者**(含自己),两人打到河牌就是两家,所以 5 + 2×2 = 9。
 * 这个数同时钉住 0105 修掉的两件事,两者各自都足以让摊牌不可见:
 *   · 亮出来的牌真的翻了过来 —— 此前对手座位硬写 `isHidden`,牌送到了也只渲染成牌背;
 *   · 手牌结束之后牌还留在桌上 —— 此前 `hand_ended` 一到就整块卸载(实测 6 毫秒后清空)。
 */
const FACE_UP_AT_SHOWDOWN = 9

test.describe('打到摊牌', () => {
  test('跟注推进三条街,公共牌逐街出现,结算面板弹出', async ({ browser }) => {
    const room = `ui-showdown-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))

    try {
      await joinTable(a, 'alice', room, 1)
      await joinTable(b, 'bob', room, 2)

      const start = a.getByRole('button', { name: /Start Game/i })
      await expect(start).toBeVisible({ timeout: 10_000 })
      await start.click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })

      // 开局后每人两张底牌只发给自己,牌面图从 public/cards 加载。
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(2, { timeout: 10_000 })

      // 一路 check/call 推进。每一步都等服务器表态,被拒就换另一个动作(见 helpers.checkOrCall),
      // 所以「没走到河牌」只会是真的走不动,不会是空转。
      const reachedRiver = await advanceHand([a, b], async () =>
        (await a.locator(FACE_UP_CARD).count()) >= FACE_UP_AT_RIVER,
      )
      expect(reachedRiver, '一路 check/call 应当能打到河牌(自己 2 张 + 公共 5 张)').toBe(true)

      // 河牌之后再走完最后一轮 → 手牌结束,结算面板弹出(0081·A 接的)
      const ended = await advanceHand([a, b], async () =>
        !(await a.locator('text=/In game/i').isVisible().catch(() => false)),
      )
      expect(ended, '最后一轮走完后手牌应当结束').toBe(true)
      await expect(a.locator('text=/本手结算/')).toBeVisible({ timeout: 10_000 })
      await expect(a.getByRole('button', { name: /Start Game|^Ready$/ })).toBeVisible({ timeout: 10_000 })

      // 摊牌留在桌上(0105)。两边都要断言:亮牌是广播,不是只发给赢家。
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(FACE_UP_AT_SHOWDOWN, { timeout: 10_000 })
      await expect(b.locator(FACE_UP_CARD)).toHaveCount(FACE_UP_AT_SHOWDOWN, { timeout: 10_000 })
      // 按座位断言比只数总数更硬:2 号位是 bob,**他那两张**朝上才算「翻开了对手的牌」,
      // 否则光数 9 张也可能是别处多渲染了两张。用既有的 data-seat 钩子,不为测试另加一个。
      await expect(a.locator('[data-seat="2"]').locator(FACE_UP_CARD)).toHaveCount(2)
      await expect(a.getByTestId('showdown-recap')).toBeVisible()

      // 关掉结算面板之后**仍然**看得见——这才是「留在桌上」的判据。`clearResult` 只清 lastResult,
      // 不碰 reveals;要是牌其实是跟着面板一起活的,这一步就会红。
      await a.getByRole('button', { name: '关闭结算' }).click()
      await expect(a.locator('text=/本手结算/')).toBeHidden()
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(FACE_UP_AT_SHOWDOWN)

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
