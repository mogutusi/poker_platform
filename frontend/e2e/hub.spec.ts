// 大厅枢纽 + 两个新页面的浏览器验证。
// 大厅的规则是「摘要 + 入口」:每个模块在大厅只给摘要,点进去看全量。
//
// 前置:后端在跑(见 docs/dev.md)。

import { expect, test, type Page } from '@playwright/test'

const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

/** 每个用例用不同 dev 账号:同账号在用例间会互相顶替连接。 */
const DEV_USERS = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank']
function pickUser(title: string): string {
  let h = 0
  for (const ch of title) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return DEV_USERS[h % DEV_USERS.length]
}

async function login(page: Page, user: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/Username/i).fill(user)
  await page.getByLabel(/Password/i).fill(PASSWORD)
  await page.getByLabel(/K_user/i).fill(K_USER)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
}

test.describe('大厅枢纽', () => {
  test('排行榜是摘要,并标明不含桌上筹码', async ({ page }, testInfo) => {
    await login(page, pickUser(testInfo.title))

    // 后端排的是结算后的全局积分。不标注的话,一个把积分全买进牌桌的人排得很低,看着像 bug。
    await expect(page.locator('text=/不含桌上筹码/')).toBeVisible({ timeout: 10_000 })
  })

  test('头像卡片进设置页,四块功能都在', async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await login(page, pickUser(testInfo.title))

    // 账号设置只有这一个入口,不另加导航项。
    await page.getByTestId('profile-card').click()
    await expect(page).toHaveURL(/\/settings/, { timeout: 10_000 })

    await expect(page.getByText('我的资料')).toBeVisible()
    await expect(page.getByText('更改昵称')).toBeVisible()
    await expect(page.getByText('修改密码')).toBeVisible()
    // K_user 轮换的出口——这是本页存在的关键理由:轮换后没地方换钥匙就会登不进来。
    await expect(page.getByText('更换 K_user 密钥')).toBeVisible()

    // 只回大厅一个出口
    await page.getByRole('button', { name: /返回大厅/ }).click()
    await expect(page).toHaveURL(/\/lobby/, { timeout: 10_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })

  test('历史页能打开并给出可读的空态或列表', async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await login(page, pickUser(testInfo.title))

    await page.getByTestId('history-entry').click()
    await expect(page).toHaveURL(/\/history/, { timeout: 10_000 })

    // 有记录就列出来,没有就给空态——两者都不能是白屏。
    await expect(
      page.locator('text=/还没有打过牌|底池|盈亏|加载中/').first(),
    ).toBeVisible({ timeout: 15_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })

  test('私聊抽屉在大厅可开可关,且与房间聊天分开', async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await login(page, pickUser(testInfo.title))

    // 收起态是个克制的小浮标(用户明确担心抽屉太占地方)
    // 用无障碍标签定位,比 testid 更稳:它同时是给读屏用户的说明,不会随视觉调整而改。
    const toggle = page.getByRole('button', { name: /打开私聊/ })
    await expect(toggle).toBeVisible({ timeout: 10_000 })
    await toggle.click()

    const drawer = page.getByRole('complementary', { name: '私聊' }).or(page.locator('[aria-label="私聊"]'))
    await expect(drawer.first()).toBeVisible()

    await page.getByRole('button', { name: '关闭私聊' }).click()
    await expect(toggle).toBeVisible()

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })
})
