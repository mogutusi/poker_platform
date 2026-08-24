// 界面上加注(0085,补 0080·B)。
//
// 到 0085 之前浏览器只走过 Check/Call —— 加注按钮从没在真界面上被点过。协议层的加注与 min-raise
// 在 npm run smoke(scripts/smoke-raise-sidepot.mjs)里验;这里验的是「界面上点下去,服务器真的接受」。
//
// 前置:后端在跑(见 docs/dev.md)。

import { expect, test } from '@playwright/test'
import { joinTable, whoActs } from './helpers'

test.describe('界面上加注', () => {
  test('输入金额点 Raise:服务器接受,底池随之变大', async ({ browser }) => {
    const room = `ui-raise-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))

    try {
      await joinTable(a, 'dave', room, 1)
      await joinTable(b, 'eve', room, 2)
      await a.getByRole('button', { name: /Start Game/i }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })

      const actor = await whoActs([a, b])
      expect(actor, '开局后应该有人可以行动').not.toBeNull()

      // 底池在加注前的值,用来断言服务器真的收了这一注(而不是界面自己画大了)
      const potBefore = await a.getByTestId('pot-amount').innerText()

      // 输入一个明确合法的目标总额(BB=2,加到 10 远高于任何下限),然后点 Raise
      const RAISE_TO = 10
      await actor!.locator('input[type="number"]').first().fill(String(RAISE_TO))
      await actor!.getByRole('button', { name: /^Raise$/ }).click()

      // 服务器接受的证据:两边的底池都变大了(广播回来的,不是本地算的)
      for (const p of [a, b]) {
        await expect(p.getByTestId('pot-amount')).not.toHaveText(potBefore, { timeout: 10_000 })
      }
      // 且没有错误提示弹出来(加注被拒时界面会显示 ILLEGAL_ACTION 的中文文案)
      await expect(a.getByTestId('action-error')).toBeHidden()

      // ── BUG-19 的正面用例(0088)──
      // 别人刚大额加注(2 → 10 ⇒ last_raise_size=8),此刻合法下限是 10+8=18。**不手动填金额**
      // 直接点 Raise:0085 之前前端会自编一个 10+BB=12 发出去,被 ILLEGAL_ACTION 拒;现在用的是
      // 服务器广播的 min_raise_to。输入框的 min 也应当是这个数,而不是 callAmount*2。
      const responder = await whoActs([a, b])
      expect(responder, '加注后对手应该可以行动').not.toBeNull()
      const amountBox = responder!.locator('input[type="number"]').first()
      expect(
        await amountBox.getAttribute('min'),
        '输入框的 min 应当是服务器给的下限(last_bet + last_raise_size = 18)',
      ).toBe('18')

      const potBeforeReraise = await a.getByTestId('pot-amount').innerText()
      await responder!.getByRole('button', { name: /^Raise$/ }).click()  // 不填金额,走服务器给的下限
      await expect(a.getByTestId('pot-amount')).not.toHaveText(potBeforeReraise, { timeout: 10_000 })
      await expect(responder!.getByTestId('action-error')).toBeHidden()

      // 再加注之后这一手还能正常往下走:加注方跟平
      const closer = await whoActs([a, b])
      expect(closer, '再加注后对手应该可以行动').not.toBeNull()
      await closer!.getByRole('button', { name: /^Call/ }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible()

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
