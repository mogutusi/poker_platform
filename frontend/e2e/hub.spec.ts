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
    await page.getByTestId('nav-lobby').click()
    await expect(page).toHaveURL(/\/lobby/, { timeout: 10_000 })

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })

  test('数据页签能打开,给出可读的空态或统计(手牌历史已搬到这里)', async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))
    await login(page, pickUser(testInfo.title))

    // 大厅不再放手牌历史;顶部导航的「数据」页签是新入口
    await expect(page.getByTestId('history-entry')).toHaveCount(0)
    await page.getByTestId('nav-stats').click()
    await expect(page).toHaveURL(/\/stats/, { timeout: 10_000 })

    // 有记录就出统计 + 热力图,没有就给空态——两者都不能是白屏。
    await expect(page.locator('text=/还没有打过牌|起手牌热力图|加载中/').first()).toBeVisible({ timeout: 15_000 })

    // 导航能回大厅(客户端跳转,会话不丢)
    await page.getByTestId('nav-lobby').click()
    await expect(page.getByTestId('profile-card')).toBeVisible({ timeout: 10_000 })
    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })
})

test.describe('完整排行页(0104)', () => {
  test('大厅入口不再是占位,点进去能看到带名次的全量榜', async ({ page }, testInfo) => {
    await login(page, pickUser(testInfo.title))

    // 0081 起这里一直是 disabled 的「待建」占位(有意不造假链接);0104 兑现它。
    const entry = page.getByTestId('leaderboard-more')  // 摘要每行用的是 leaderboard-entry,别撞
    await expect(entry).toBeEnabled()
    await entry.click()

    await expect(page.getByText('完整排行')).toBeVisible()
    // 默认是按净赢的「总榜」;积分排名(和大厅摘要同一口径)在「积分」页签
    await expect(page.getByRole('tab', { name: '总榜' })).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('tab', { name: '积分' }).click()
    const rows = page.getByTestId('leaderboard-row')
    await expect(rows.first()).toBeVisible({ timeout: 10_000 })

    // 名次由后端给,前端不重排:第一行必须是 #1
    await expect(rows.first()).toContainText('#1')
    // 详情页必须和大厅摘要用同一套口径,否则两处说法不一
    await expect(page.getByText(/不含桌上筹码/)).toBeVisible()

    // 详情页要比大厅摘要长(摘要只给 5 条),否则「点进去看全量」这条规则没兑现
    expect(await rows.count()).toBeGreaterThan(1)

    await page.getByTestId('nav-lobby').click()
    await expect(page.getByTestId('leaderboard-more')).toBeVisible()
  })
})
