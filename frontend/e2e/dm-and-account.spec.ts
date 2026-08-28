// 私聊两人真收发 + 历史页有真实记录 + 改密真执行(0081·C)。
//
// 为什么单开一个 spec:hub.spec 里那两条相关用例停在「一个人开关抽屉」和「空态或列表都算过」——
// 前者没有第二个人,发送/投递/未读/已读一条都没验;后者空态永远绿。这里补的是真正会失败的那部分。
//
// 账号:eve / frank,**全程不进房**。所以不会踩「局中离房要等手牌打完、下一个用例进不去房」
// 那个坑(0089 的教训),也不给后面的用例留残局。
//
// 前置:后端在跑(见 service/docs/dev.md)。

import { expect, test, type Page } from '@playwright/test'
import { K_USER, PASSWORD, login } from './helpers'

/** 打开私聊抽屉(按钮的 aria-label 会随未读数变,所以用前缀匹配)。 */
async function openDrawer(page: Page): Promise<void> {
  await page.getByRole('button', { name: /^打开私聊/ }).click()
  await expect(page.getByRole('complementary', { name: '私聊' })).toBeVisible()  // 抽屉是 <aside>
}

/** 在抽屉里开一个到 peer 的新会话。 */
async function startConversation(page: Page, peer: string): Promise<void> {
  await page.getByLabel('新私聊对象昵称').fill(peer)
  await page.getByLabel('新私聊对象昵称').press('Enter')
  await expect(page.getByLabel('私聊输入')).toBeVisible()
}

test.describe('私聊(两人真收发)', () => {
  test('一个人发,另一个人真的收到;读掉之后发件人看到已读', async ({ browser }) => {
    // 两个独立浏览器上下文 = 两个真用户,各自的 localStorage / ws 连接互不干扰。
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const eve = await ctxA.newPage()
    const frank = await ctxB.newPage()

    try {
      await login(eve, 'eve')
      await login(frank, 'frank')

      // 正文带时间戳:这个库是**跨用例累积**的(私信落库、已读才清),写死文本会和上一次跑的混在一起。
      const text = `hello-from-eve-${Date.now()}`

      await openDrawer(eve)
      await startConversation(eve, 'frank')
      await eve.getByLabel('私聊输入').fill(text)
      await eve.getByLabel('私聊输入').press('Enter')

      // 判据一:发件人自己看到这条(本地回显)。.first():正文同时出现在气泡与左侧会话预览里。
      await expect(eve.getByText(text).first()).toBeVisible()

      // 判据二:**收件人真的收到了**——这是 hub.spec 那条单人用例完全够不到的部分。
      // 未读徽标反映在抽屉按钮的 aria-label 上(未读数 > 0 时文案不同)。
      await expect(frank.getByRole('button', { name: /^打开私聊,\d+ 条未读/ })).toBeVisible({ timeout: 10_000 })

      await openDrawer(frank)
      await frank.getByRole('button', { name: /eve/ }).first().click()
      await expect(frank.getByText(text).first()).toBeVisible()

      // 判据三:frank 读掉之后,eve 侧应看到已读回执(DMRead → 气泡上的已读标记)。
      // 回执是异步的,给它时间;拿不到就说明 dm_mark_read 那条链断了。
      await expect(eve.getByText('已读').first()).toBeVisible({ timeout: 10_000 })
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})

test.describe('私聊(离线补收)', () => {
  test('收件人整个人不在线时发的私信,他登录后被补收进来', async ({ browser }) => {
    // 这条走的是 0040 的**另一条臂**:收件人不在线 → 落库(未读)→ 登录时 deliver_dm_catch_up
    // 读 DB 补发。上面那条实时用例覆盖不到它——那里 frank 一直在线,消息走的是在线路由。
    // 账号换 carol/dave,不复用 eve/frank:同一对账号的私信库存跨用例累积,未读计数会互相污染。
    // 两人在本用例全程不进房(0089 的占座纪律只约束进房用例)。
    const ctxSender = await browser.newContext()
    const carol = await ctxSender.newPage()

    try {
      await login(carol, 'carol')
      const text = `while-you-were-out-${Date.now()}`

      // dave 此刻没有任何上下文——不是「没开抽屉」,是整个人不在线。
      await openDrawer(carol)
      await startConversation(carol, 'dave')
      await carol.getByLabel('私聊输入').fill(text)
      await carol.getByLabel('私聊输入').press('Enter')
      await expect(carol.getByText(text).first()).toBeVisible() // 发送成立(本地回显)

      // 现在 dave 才上线。没有人重发任何东西——接下来出现的一切都只能来自登录补收。
      const ctxLate = await browser.newContext()
      const dave = await ctxLate.newPage()
      try {
        await login(dave, 'dave')
        // 未读徽标:补收链把落库的那条推了过来(aria-label 随未读数变)。
        await expect(dave.getByRole('button', { name: /^打开私聊,\d+ 条未读/ })).toBeVisible({ timeout: 10_000 })
        await openDrawer(dave)
        await dave.getByRole('button', { name: /carol/ }).first().click()
        // 断言那条**准确的文本**:补收要是丢内容、错会话,靠徽标是看不出来的。
        await expect(dave.getByText(text).first()).toBeVisible()
      } finally {
        await ctxLate.close()
      }
    } finally {
      await ctxSender.close()
    }
  })
})

test.describe('历史页(有真实记录时)', () => {
  test('列表渲染出真实的手牌行,而不是只验空态', async ({ page }) => {
    // /hands 是全局的:冒烟脚本与既有浏览器用例已经打过很多手,所以这里应当有真实记录。
    // 若库真的是空的(全新机器),用例给出可辨认的失败信息,而不是悄悄绿掉。
    await login(page, 'eve')
    // **必须点进去,不能 page.goto**:会话只在内存里(transport/session.ts),整页加载会把它清掉,
    // 路由守卫随即把你弹回登录页——三条用例第一版全栽在这上面。
    await page.getByTestId('history-entry').click()

    await page.getByRole('button', { name: '全部' }).click()  // 默认是「我的」;全局记录在「全部」页签

    // 页面要么给出记录、要么给出空态,先等它决出一个,避免在 loading 上断言
    const empty = page.getByText('还没有任何牌局记录')
    const pot = page.getByText(/底池/).first()
    await expect(empty.or(pot)).toBeVisible({ timeout: 15_000 })

    expect(await empty.count(), '库里应当已有手牌记录(冒烟与既有浏览器用例打过);为空说明这是全新库,先跑一次 npm run smoke').toBe(0)
    await expect(pot).toBeVisible()
    await expect(pot.locator('..')).toContainText(/\d/)  // 真实数字,不是空壳
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
