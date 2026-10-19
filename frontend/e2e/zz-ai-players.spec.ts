// AI 对手:从牌桌页的「AI 对手」面板加两个 AI,开局,自己跟着打,AI 自己行动,直到这一手结算;最后让 AI 离桌。
//
// AI 是 scripts/bots.mjs(由 /api/bots 拉起),走的是和浏览器同一条加密协议,所以这条用例同时验了
// 「机器人真的会行动」和「整手牌在多人桌上能打完」。
//
// 文件名带 zz- 前缀是有意的:Playwright 按文件名顺序串行跑,AI 用的是 bob/carol 等 dev 账号,
// 放在最后跑,AI 离桌之后不会和别的用例抢账号。账号:dave(AI 会排除掉他)。前置:后端在跑、`next dev`。

import { expect, test } from '@playwright/test'
import { checkOrCall, login, whoActs } from './helpers'

test.describe('AI 对手', () => {
  test('加两个 AI → 开局 → AI 自己行动,一手打到结算 → AI 离桌', async ({ page }) => {
    test.setTimeout(120_000)
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))

    await login(page, 'dave')
    const room = `ui-ai-${Date.now().toString(36)}`
    await page.getByLabel('房间名').fill(room)
    await page.getByTestId('enter-room').click()
    await expect(page.locator('text=/观战中/')).toBeVisible({ timeout: 15_000 })
    await page.locator('[data-empty-seat="6"]').click()
    await expect(page.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
    await page.getByRole('button', { name: '买入' }).click()
    await page.getByRole('button', { name: /^Ready$/ }).click()

    await page.getByRole('button', { name: /AI 对手/ }).click()
    await page.getByRole('button', { name: '2', exact: true }).click()
    await page.getByRole('button', { name: /加入 2 个 AI/ }).click()

    // 两个 AI 入座、买入、准备之后才会出现开局按钮(全员已准备)
    await expect(page.getByText(/在座 3\/9/)).toBeVisible({ timeout: 30_000 })
    const start = page.getByRole('button', { name: /Start Game/ })
    await expect(start).toBeVisible({ timeout: 20_000 })
    await start.click()
    await expect(page.locator('text=/In game/')).toBeVisible({ timeout: 10_000 })

    // 轮到自己就过牌/跟注,其余时间等 AI 自己出手;一手牌必须能打到结算
    const result = page.locator('text=/本手结算/')
    for (let i = 0; i < 120 && !(await result.isVisible()); i++) {
      if ((await whoActs([page])) === page) await checkOrCall(page)
      else await page.waitForTimeout(500)
    }
    await expect(result).toBeVisible({ timeout: 30_000 })

    // 让 AI 离桌:面板回到「加入」状态
    await page.getByRole('button', { name: /AI 对手/ }).click()
    await page.getByRole('button', { name: '让 AI 离桌' }).click()
    await expect(page.getByRole('button', { name: /^AI 对手$/ })).toBeVisible({ timeout: 15_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })
})
