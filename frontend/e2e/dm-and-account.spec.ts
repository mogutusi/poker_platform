// 历史页有真实记录 + 改密真执行(0081·C)。私信的两条用例已随私信界面一起移除(只保留房聊,
// 房聊用例见 chat.spec.ts)。
//
// 为什么单开一个 spec:hub.spec 里那条历史页用例「空态或列表都算过」,空态永远绿。这里补的是真正会失败的那部分。
//
// 账号:eve,**全程不进房**。所以不会踩「局中离房要等手牌打完、下一个用例进不去房」
// 那个坑(0089 的教训),也不给后面的用例留残局。
//
// 前置:后端在跑(见 service/docs/dev.md)。

import { expect, test } from '@playwright/test'
import { K_USER, PASSWORD, login } from './helpers'

test.describe('数据页的手牌记录(有真实记录时)', () => {
  test('最近手牌一行能点开成整手:底池、公共牌、各家盈亏', async ({ page }) => {
    // eve 是 AI 对手用的 dev 账号之一,跑过冒烟 / AI 用例之后她名下就有手牌。全新库上她一手没打过,
    // 那就只能验空态,并在这里说清楚(而不是悄悄绿掉)。
    await login(page, 'eve')
    // **必须点进去,不能 page.goto**:会话只在内存里(transport/session.ts),整页加载会把它清掉。
    await page.getByTestId('nav-stats').click()

    const empty = page.getByText('还没有打过牌')
    const rows = page.getByTestId('history-row')
    await expect(empty.or(rows.first())).toBeVisible({ timeout: 15_000 })
    if (await empty.count()) {
      test.info().annotations.push({ type: 'note', description: 'eve 还没有手牌记录(全新库),只验了空态' })
      return
    }
    // 统计格子和热力图都在
    await expect(page.locator('[data-stat="VPIP"]')).toBeVisible()
    await expect(page.getByTestId('hand-heatmap')).toBeVisible()

    await rows.first().click()
    const pot = page.getByText(/底池/).first()
    await expect(pot).toBeVisible({ timeout: 10_000 })
    await expect(pot.locator('..')).toContainText(/\d/)  // 真实数字,不是空壳
    await expect(page.getByTestId('hand-player').first()).toBeVisible()
  })
})

test.describe('设置页(改密码真执行)', () => {
  test('把密码改成同一个值:整条链跑通,并如实提示其它设备已被登出', async ({ page }) => {
    // 改成**同一个值**,所以幂等、可重复跑、也不弄脏共享的 dev 账号状态。
    // 验的是「表单 → 加密信封 → 后端 → 成功提示」这条链真的通,以及 0097 之后的成功文案。
    await login(page, 'frank')
    await page.getByTestId('profile-card').click()  // 同上:点进去,不 goto

    await page.getByLabel('旧密码').fill(PASSWORD)
    await page.getByLabel('新密码').fill(PASSWORD)
    await page.getByRole('button', { name: '保存密码' }).click()

    await expect(page.getByText(/密码已更新/)).toBeVisible({ timeout: 10_000 })
    // 0097 起改密会吊销别处会话,成功文案必须如实说出来——否则对面被踢回登录页时对不上因果。
    // 断言落在**成功提示**那条上(表单上方的静态说明里也有「其它设备」,故不能裸匹配)。
    await expect(page.getByText(/密码已更新。你在其它设备上的登录已被退出/)).toBeVisible()
  })
})

// K_USER 由 helpers.login 使用;此处 re-export 以免 lint 认为导入未用。
export { K_USER }
