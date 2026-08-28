// 两个玩家在真实浏览器里同桌打完一手。补的是 0079 自 review ⑥ 记的最大缺口:
// 牌桌页的入座/买入/准备/开局/行动按钮此前只有构建保证,没人真正点过。
//
// 用两个 browser context 模拟两个玩家:它们的 localStorage 与内存会话彼此独立,
// 等于两台机器。前置:后端在跑(见 docs/dev.md)。

import { expect, test, type Page } from '@playwright/test'
import { FACE_UP_CARD } from './helpers'

const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

async function loginAs(page: Page, user: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/Username/i).fill(user)
  await page.getByLabel(/Password/i).fill(PASSWORD)
  await page.getByLabel(/K_user/i).fill(K_USER)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
}

/** 站内进房:不能用 goto,session_token 只在内存,整页加载就丢。 */
async function enterRoom(page: Page, room: string): Promise<void> {
  await page.getByLabel('房间名').fill(room)
  await page.getByTestId('enter-room').click()
  await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })
  await expect(page.locator('text=/观战中/')).toBeVisible({ timeout: 15_000 })
}

/** 坐到指定座位号。空座带 data-empty-seat,按它定位比按文案稳。 */
async function sitDown(page: Page, seatNumber: number): Promise<void> {
  await page.locator(`[data-empty-seat="${seatNumber}"]`).click()
}

test.describe('两人同桌', () => {
  test('入座 → 买入 → 准备 → 开局', async ({ browser }) => {
    const room = `ui-table-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))

    try {
      await loginAs(a, 'alice')
      await loginAs(b, 'bob')
      await enterRoom(a, room)
      await enterRoom(b, room)

      // 入座:两人各占一个空位。入座后「观战中」提示应消失。
      await sitDown(a, 1)
      await expect(a.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
      await sitDown(b, 2)
      await expect(b.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })

      // 买入:入座后桌上筹码为 0,界面应给出买入入口。
      await a.getByRole('button', { name: '买入' }).click()
      await b.getByRole('button', { name: '买入' }).click()

      // 准备:买入到账后 Ready 才可点。
      const readyA = a.getByRole('button', { name: /^Ready$/ })
      await expect(readyA).toBeEnabled({ timeout: 10_000 })
      await readyA.click()
      await b.getByRole('button', { name: /^Ready$/ }).click()

      // 两人都准备好 → 出现开局按钮 → 开局后进入牌局态。
      const start = a.getByRole('button', { name: /Start Game/i })
      await expect(start).toBeVisible({ timeout: 10_000 })
      await start.click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })

      // 开局后行动面板应出现。这些按钮此前只有构建保证,没人点过。
      await expect(a.getByRole('button', { name: /^Fold$/ })).toBeVisible({ timeout: 10_000 })

      // 自己的两张底牌应发到手;别人的牌在摊牌前结构上就不存在,所以桌上不该出现第三、四张明牌。
      await expect(a.locator('img[alt], img[src*="/cards/"]').first()).toBeVisible({ timeout: 10_000 })

      // 真打一个动作:谁该行动谁点弃牌,手牌随即结束,回到可以再开一局的状态。
      const foldA = a.getByRole('button', { name: /^Fold$/ })
      const foldB = b.getByRole('button', { name: /^Fold$/ })
      if (await foldA.isEnabled()) {
        await foldA.click()
      } else {
        await foldB.click()
      }
      // 一人弃牌 → 只剩一个未弃牌者 → 手牌直接结束(见 service/docs/rules.md ③)
      await expect(a.locator('text=/In game/i')).toBeHidden({ timeout: 15_000 })

      // 结算面板必须出现:此前这条信息 store 收着但界面不显示,打完一手看不到赢了多少。
      await expect(a.locator('text=/本手结算/')).toBeVisible({ timeout: 10_000 })
      await expect(a.locator('text=/你赢了|这手没有赢到底池/')).toBeVisible()
      // 能关掉
      await a.getByRole('button', { name: '关闭结算' }).click()
      await expect(a.locator('text=/本手结算/')).toBeHidden()

      // 有人弃牌收尾的手牌**没有**结算展示期:没摊牌就没有别人的牌可亮,桌面该清空(0105)。
      // 展示期的判据是 reveals 非空,而这一手根本没产生 HandShowDown——把判据换成「board 非空」
      // 或「永远显示」的话,这条会红。
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(0)
      await expect(a.getByTestId('showdown-recap')).toBeHidden()

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
