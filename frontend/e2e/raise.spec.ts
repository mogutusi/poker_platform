// 界面上加注(0085,补 0080·B)。
//
// 到 0085 之前浏览器只走过 Check/Call —— 加注按钮从没在真界面上被点过。协议层的加注与 min-raise
// 在 npm run smoke(scripts/smoke-raise-sidepot.mjs)里验;这里验的是「界面上点下去,服务器真的接受」。
//
// 前置:后端在跑(见 docs/dev.md)。

import { expect, test, type Page } from '@playwright/test'

const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

async function joinTable(page: Page, user: string, room: string, seat: number): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/Username/i).fill(user)
  await page.getByLabel(/Password/i).fill(PASSWORD)
  await page.getByLabel(/K_user/i).fill(K_USER)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
  await page.getByLabel('房间名').fill(room)
  await page.getByTestId('enter-room').click()
  await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })
  await page.locator(`[data-empty-seat="${seat}"]`).click()
  await expect(page.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
  await page.getByRole('button', { name: '买入' }).click()
  const ready = page.getByRole('button', { name: /^Ready$/ })
  await expect(ready).toBeEnabled({ timeout: 10_000 })
  await ready.click()
}

/** 现在轮到哪一页行动。按钮只在自己回合可用,所以这等于「谁能点谁就是行动者」(0080·C 的红利)。 */
async function whoActs(pages: Page[]): Promise<Page | null> {
  for (const p of pages) {
    const fold = p.getByRole('button', { name: /^Fold$/ })
    if ((await fold.isVisible().catch(() => false)) && (await fold.isEnabled().catch(() => false))) return p
  }
  return null
}

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

      // 加注之后这一手还能正常往下走:对手跟注,行动权回到加注方
      const responder = await whoActs([a, b])
      expect(responder, '加注后对手应该可以行动').not.toBeNull()
      await responder!.getByRole('button', { name: /^Call/ }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible()

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
