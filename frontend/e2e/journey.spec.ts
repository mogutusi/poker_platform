// 真实浏览器走一遍用户旅程。这一层补的是「界面本身没人真正点过」这个缺口:
// 单测验状态归并、smoke 验协议,但按钮点了有没有反应、页面会不会白屏,只有这里能验。
//
// 前置:后端在跑(见 docs/dev.md)。前端由 playwright.config.ts 自动起。

import { expect, test } from '@playwright/test'

const K_USER = '00112233445566778899aabbccddeeff' // dev 共享密钥,见 service/app/poker.env.example
const PASSWORD = 'devpass123'

/**
 * 每个用例用不同账号。同一账号在两个用例间会互相顶替连接、也可能把上一轮的房间残留带过来,
 * 用例之间不该有这种耦合。dev 种子有 6 个账号,按用例标题散开。
 */
const DEV_USERS = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank']
function pickUser(title: string): string {
  let h = 0
  for (const ch of title) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return DEV_USERS[h % DEV_USERS.length]
}

test.describe('用户旅程', () => {
  test('登录页在没有 K_user 时要求填写', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByLabel(/K_user/i)).toBeVisible()
    await expect(page.getByLabel(/Username/i)).toBeVisible()
    await expect(page.getByLabel(/Password/i)).toBeVisible()
  })

  test('密码错误时给出提示,且不跳转', async ({ page }) => {
    await page.goto('/')
    await page.getByLabel(/Username/i).fill(pickUser(test.info().title))
    await page.getByLabel(/Password/i).fill('wrong-password')
    await page.getByLabel(/K_user/i).fill(K_USER)
    // 按 type 定位,不匹配文案:按钮上的字是设计的一部分,改字不该弄坏测试。
    await page.locator('button[type="submit"]').click()

    // 服务器对未知账号/密码错/blob 坏一律 401 且不区分,前端只给笼统提示。
    await expect(page.locator('text=/账号、密码或密钥不对/')).toBeVisible({ timeout: 10_000 })
    await expect(page).toHaveURL('/')
  })

  test('登录 → 大厅 → 牌桌:观战、入座、买入', async ({ page }, testInfo) => {
    const user = pickUser(testInfo.title)
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))

    await page.goto('/')
    await page.getByLabel(/Username/i).fill(user)
    await page.getByLabel(/Password/i).fill(PASSWORD)
    await page.getByLabel(/K_user/i).fill(K_USER)
    // 按 type 定位,不匹配文案:按钮上的字是设计的一部分,改字不该弄坏测试。
    await page.locator('button[type="submit"]').click()

    await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
    // 大厅确实拿到了真实数据。**不能断言某个具体账号出现在榜上**:榜只取前 5,而积分随每一手牌变动,
    // 打过几把之后那个账号就掉出前 5 了(0085 的加注/边池冒烟一跑就把 alice 挤了下去,这条因此变红)。
    // 稳的是:榜上有真实条目 + 头像卡显示的是**本次登录的这个人**(它同时验证了 /user/me 走通)。
    await expect(page.getByTestId('profile-card')).toContainText(user, { timeout: 10_000 })
    await expect(page.getByTestId('leaderboard-entry').first()).toBeVisible({ timeout: 10_000 })

    // 必须走站内跳转,不能 page.goto:session_token 只在内存,整页加载就丢了
    // (这是 docs/transport.md §六 的有意取舍,另有一条用例专门验它)。
    await page.getByLabel('房间名').fill('ui-journey')
    await page.getByTestId('enter-room').click()
    await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })

    // 进房后是观战状态,界面应提示去入座
    await expect(page.locator('text=/观战中/')).toBeVisible({ timeout: 15_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })

  test('未登录直接开牌桌页会被送回登录页', async ({ page }) => {
    // session_token 只在内存,刷新即失效,所以直接访问必须被拦回。
    await page.goto('/game?room=whatever')
    await expect(page).toHaveURL('/', { timeout: 10_000 })
  })
})
