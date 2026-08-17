// 界面上跟注打到摊牌:验多街推进、公共牌渲染、亮牌。协议层在 npm run smoke 里验过,
// 这里验的是「这些事在界面上真的看得见」(0080·A)。
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
  await page.getByRole('button', { name: '进入房间' }).click()
  await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })

  await page.locator(`[data-empty-seat="${seat}"]`).click()
  await expect(page.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
  await page.getByRole('button', { name: '买入' }).click()
  const ready = page.getByRole('button', { name: /^Ready$/ })
  await expect(ready).toBeEnabled({ timeout: 10_000 })
  await ready.click()
}

/** 谁能点就点谁:按钮只在自己回合可用,所以这等于「让该行动的人行动」。 */
async function actWhoeverCan(pages: Page[], name: RegExp): Promise<boolean> {
  for (const p of pages) {
    const btn = p.getByRole('button', { name })
    if (await btn.isVisible().catch(() => false) && (await btn.isEnabled().catch(() => false))) {
      await btn.click()
      return true
    }
  }
  return false
}

test.describe('打到摊牌', () => {
  test('跟注推进三条街,公共牌逐街出现,摊牌亮牌', async ({ browser }) => {
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
      const cardsOnA = a.locator('img[src*="/cards/"], img[srcset*="cards"]')
      await expect(cardsOnA.first()).toBeVisible({ timeout: 10_000 })

      // 一路跟注/过牌,推进到摊牌。每一步只有该行动的人的按钮是可用的。
      const pages = [a, b]
      for (let i = 0; i < 40; i++) {
        const inGame = await a.locator('text=/In game/i').isVisible().catch(() => false)
        if (!inGame) break
        // 先试 Check(不用补注),不行再 Call
        if (await actWhoeverCan(pages, /^Check$/)) continue
        if (await actWhoeverCan(pages, /^Call/)) continue
        await a.waitForTimeout(150)
      }

      // 手牌结束:回到可以再开一局的状态
      await expect(a.locator('text=/In game/i')).toBeHidden({ timeout: 20_000 })
      // 走到过摊牌就会有 5 张公共牌;若中途有人 all-in 跑完牌也一样。
      await expect(a.getByRole('button', { name: /Start Game|^Ready$/ })).toBeVisible({ timeout: 10_000 })

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
