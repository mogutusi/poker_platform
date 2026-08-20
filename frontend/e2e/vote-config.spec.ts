// 免盲投票与房间参数配置的浏览器验证(规则见 service/docs/rules.md ① / core.md）。
//
// 前置:后端在跑。

import { expect, test, type Page } from '@playwright/test'

const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

async function joinRoom(page: Page, user: string, room: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/Username/i).fill(user)
  await page.getByLabel(/Password/i).fill(PASSWORD)
  await page.getByLabel(/K_user/i).fill(K_USER)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
  await page.getByLabel('房间名').fill(room)
  await page.getByTestId('enter-room').click()
  await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })
}

async function seatAndReady(page: Page, seat: number): Promise<void> {
  await page.locator(`[data-empty-seat="${seat}"]`).click()
  await expect(page.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
  await page.getByRole('button', { name: '买入' }).click()
  const ready = page.getByRole('button', { name: /^Ready$/ })
  await expect(ready).toBeEnabled({ timeout: 10_000 })
  await ready.click()
}

test.describe('房间参数', () => {
  test('两手之间能改小盲,大盲跟着 2× 变', async ({ page }) => {
    const room = `cfg-${Date.now().toString(36)}`
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))

    await joinRoom(page, 'alice', room)

    // 任何在房成员都能改,没有房主——观战状态下入口就该在。
    await page.getByRole('button', { name: '房间设置' }).click()
    await expect(page.getByText(/当前:小盲/)).toBeVisible()

    await page.locator('#cfg-sb').fill('7')
    // 大盲由 2×小盲派生,不单独设,界面要先把算出来的值给人看
    await expect(page.getByText('大盲将变成 14')).toBeVisible()
    await page.getByRole('button', { name: '改' }).first().click()

    // 服务器广播 room_config_changed 后,面板上的「当前」应更新
    await expect(page.getByText(/小盲 7 \/ 大盲 14/)).toBeVisible({ timeout: 10_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })
})

test.describe('免盲投票', () => {
  test('有新人时可发起,投票人能表态', async ({ browser }) => {
    const room = `vote-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))

    const ctxC = await browser.newContext()
    const c = await ctxC.newPage()
    c.on('pageerror', (e) => errors.push(`C: ${e}`))

    try {
      // 关键前提:**新开的房里所有人都是 new_here,没有合格投票人**,这时开票必被
      // CANNOT_OPEN_VOTE 拒。要先让 A、B 打完一手变成「已入局」,他们才有投票权。
      await joinRoom(a, 'alice', room)
      await seatAndReady(a, 1)
      await joinRoom(b, 'bob', room)
      await seatAndReady(b, 2)

      // 打完一手:A、B 就此成为已入局玩家
      await a.getByRole('button', { name: /Start Game/i }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })
      for (const p of [a, b]) {
        const fold = p.getByRole('button', { name: /^Fold$/ })
        if (await fold.isEnabled().catch(() => false)) {
          await fold.click()
          break
        }
      }
      await expect(a.locator('text=/In game/i')).toBeHidden({ timeout: 15_000 })
      await a.getByRole('button', { name: '关闭结算' }).click().catch(() => undefined)

      // 手尾服务端把 PLAYING 改回 SITTING_IN 并广播(0082),所以要重新准备才算投票人
      for (const p of [a, b]) {
        await p.getByRole('button', { name: /^Ready$/ }).click()
      }

      // C 这时进来入座,他才是 new_here 候选
      await joinRoom(c, 'carol', room)
      await c.locator('[data-empty-seat="3"]').click()

      // 入口在两手之间一直可见:前端无法可靠知道 new_here(它只在 StateSnapshot 里,
      // 服务端重标时不发事件),所以不预判能不能成,发出去让服务器裁决。
      const openBtn = a.getByRole('button', { name: '发起免盲投票' })
      await expect(openBtn).toBeVisible({ timeout: 15_000 })
      await openBtn.click()

      // 面板要说清在投什么、还差谁——全票制下「还差谁」比「几票」有用。
      const panel = a.getByRole('dialog', { name: '免盲投票' })
      await expect(panel).toBeVisible({ timeout: 10_000 })
      await expect(panel.getByText(/全票同意才通过/)).toBeVisible()
      await expect(panel.getByText(/已同意 \d+ \/ \d+/)).toBeVisible()

      // 投票人能表态;投完变成「等其他人」
      const agree = panel.getByRole('button', { name: '同意' })
      if (await agree.isVisible().catch(() => false)) {
        await agree.click()
        await expect(panel.getByText(/你已同意/)).toBeVisible({ timeout: 10_000 })
      }

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
      await ctxC.close()
    }
  })
})
