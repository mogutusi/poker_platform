// 免盲投票与房间参数配置的浏览器验证(规则见 service/docs/rules.md ① / core.md）。
//
// 前置:后端在跑。

import { expect, test, type Page } from '@playwright/test'
import { FACE_UP_CARD, dropGameSocket, gameSockets, installWsProbe, seatPoints } from './helpers'

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

  test('买入额:越界被服务器拒且不生效,合法值改完就是入座的默认买入', async ({ page }) => {
    const room = `cfg-buyin-${Date.now().toString(36)}`
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(String(e)))

    // **专属账号**:这一条要真的进房,而 table/raise/reconnect 那几条用例**结束时还停在手牌里**。
    // 同名账号再登录会走「先退再进」,但局中的 leave_room 要等这手打完才驱逐(rules.md ④),
    // 于是进不去房、连「房间设置」入口都不出现——在全套里必红、单跑却绿(0089 实测撞到)。
    await joinRoom(page, 'gina', room)
    await page.getByRole('button', { name: '房间设置' }).click()
    const buyInField = page.locator('#cfg-bi')
    const submitBuyIn = page.getByRole('button', { name: '改' }).last()

    // 越界:上下限归 shell 按 gameconfig 挡(MAX_BUY_IN=100000000),core 不 import 配置。
    // 前端**不预判**——照发,让服务器裁决,再把 INVALID_BUY_IN 翻成人话。
    await buyInField.fill('100000001')
    await submitBuyIn.click()
    await expect(page.getByTestId('action-error')).toHaveText(/买入额超出允许范围/, { timeout: 10_000 })
    await expect(page.getByText(/买入 100/)).toBeVisible()  // 被拒 ⇒ 当前值一分没动(dev 房默认 100)
    await page.getByTestId('action-error').click()  // 消掉提示,免得下一条断言看的是旧的

    // 合法值:广播回来之后面板跟着变,而且**它就是入座时真的买进去的那个数**
    await buyInField.fill('250')
    await submitBuyIn.click()
    await expect(page.getByText(/买入 250/)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('action-error')).toBeHidden()

    await seatAndReady(page, 1)
    expect(await seatPoints(page, 1), '入座买入用的应当是改过的房间默认值').toBe(250)

    expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
  })
})

test.describe('免盲投票', () => {
  test('有新人时可发起,投票人能表态,投到通过后新人真的免盲入局', async ({ browser }) => {
    // 这一条要走完「打一手 → 开票 → 掉线重连 → 再表态 → 通过 → 再开一手」,比默认 30 秒长。
    test.setTimeout(90_000)
    const room = `vote-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    await ctxB.addInitScript(installWsProbe)  // 下面要在 B 上制造一次掉线(BUG-9)
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

      // 开局前:两人都是新人,座位上应挂「等入局」(值来自服务器的 new_here,不是前端猜的)
      await expect(a.locator('[data-owes-entry]')).toHaveCount(2)

      // 打完一手:A、B 就此成为已入局玩家
      await a.getByRole('button', { name: /Start Game/i }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })
      // 0084 的正题:开局末尾服务端清掉被发牌者的 new_here 并**广播**。此前这一步没有任何事件承载,
      // 客户端那份来自进房快照的标志会一直挂着不掉 —— 这条断言就是钉住它。
      await expect(a.locator('[data-owes-entry]')).toHaveCount(0, { timeout: 10_000 })
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
      await seatAndReady(c, 3)  // 要开下一手,C 也得买入 + 准备(他是 new_here,所以仍是候选而不是投票人)

      // A 那边应看到 C 挂上「等入局」(sit_down 的 user_status_changed 现在自带 new_here)
      await expect(a.locator('[data-owes-entry]')).toHaveCount(1, { timeout: 15_000 })

      // 入口在两手之间一直可见:0084 之后候选能如实显示了,但「有没有合格投票人」仍是规则,
      // 前端不算 —— 照旧发出去让服务器裁决,只是顺带告诉用户桌上有几个人在等入局。
      const openBtn = a.getByTestId('open-free-entry-vote')
      await expect(openBtn).toBeVisible({ timeout: 15_000 })
      await expect(openBtn).toContainText('1 人等入局')
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

      // ── BUG-9(0088):掉线重连之后这张票必须还在 ──
      // 服务器重连只私发 StateSnapshot、不重发投票事件,所以快照不投影投票的话面板会凭空消失。
      // 全票制下,消失的那一份恰恰属于「还没表态、正被等着的人」,票就此永久卡住。
      const panelB = b.getByRole('dialog', { name: '免盲投票' })
      await expect(panelB).toBeVisible({ timeout: 10_000 })  // 断线前 B 也看得见这张票
      const socketsBefore = await dropGameSocket(b)
      await expect
        .poll(async () => (await gameSockets(b)).length, { timeout: 20_000 })
        .toBeGreaterThan(socketsBefore)

      // 面板还在,而且还是同一张票(候选没变)。这就是 BUG-9 的正题:此前重连之后它会凭空消失。
      await expect(panelB).toBeVisible({ timeout: 15_000 })
      await expect(panelB.getByText(/carol/)).toBeVisible()

      // 但**投票权确实没了**,而且这是对的:重连恢复到 SITTING_IN 而不是 READY_TO_PLAY
      // (见 service/docs/connection.md 重连臂),所以他此刻不是合格投票人。
      await expect(panelB.getByText(/你不是本次的投票人/)).toBeVisible({ timeout: 10_000 })

      // 他重新点 Ready 才又成为投票人 —— 而这件事必须有事件说出来(0088 给 _maybe_resolve_entry_vote
      // 补的那条 FreeEntryVoteUpdated)。没有它,面板会一直停在「你不是本次的投票人」,
      // 全票制下这张票就永远等不到他,卡死。
      await b.getByRole('button', { name: /^Ready$/ }).click()
      await expect(panelB.getByRole('button', { name: '同意' })).toBeVisible({ timeout: 10_000 })

      // ── 0089:把票投到通过,看结果是不是真的落地 ──
      // 全票制:A 已同意,B 这一票投下去就够了。
      await panelB.getByRole('button', { name: '同意' }).click()

      // 结果要说出来。此前 free_entry_vote_closed 只被用来关面板,passed/waived 直接丢掉——
      // 面板凭空消失,谁也不知道自己那一票有没有起作用、谁被免了。
      for (const p of [a, b, c]) {
        const result = p.getByTestId('free-entry-vote-result')
        await expect(result).toBeVisible({ timeout: 10_000 })
        await expect(result).toContainText('通过')
        await expect(result).toContainText('carol')
      }

      // 免掉的那笔钱真的没收:开一手,底池只有小盲 + 大盲(1+2=3)。
      // C 若没被免,他入局要**额外**付一个大盲,底池就是 5——除非他这手恰好坐在盲位上
      // (三人桌两个盲位,位置由庄家轮转决定,界面上没有办法指定)。所以这条断言是**必要条件**:
      // 免盲生效时它恒为真,不生效时多数情况下会红。「一分不多收」的穷举在 core 层
      // (tests/core/test_free_entry_vote.py 的 waive 用例)。
      await a.getByRole('button', { name: /Start Game/i }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })
      await expect(c.locator(FACE_UP_CARD)).toHaveCount(2, { timeout: 10_000 })  // C 真的被发牌了
      await expect(a.getByTestId('pot-amount')).toHaveText('3', { timeout: 10_000 })

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
      await ctxC.close()
    }
  })
})
