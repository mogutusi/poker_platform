// 断线重连与顶替,在真浏览器里验(0079·B)。
//
// 0083 把顶替 / 慢客户端丢连 / 退出清理三条路径大改了一遍,至今只有后端测试盖着,而后端测试
// 用的是 FakeWS —— 连「关一条 socket 需要时间」都要专门造个 _SlowCloseWS 才模拟得出来。
// 这一层验的是浏览器里真的断一次线会发生什么:
//   ① seq 跨重连继续累加(从 0 重来 → 服务器判 stale_seq → 关连接 4400 → 死循环)
//   ② 重连后的 StateSnapshot 把桌子重新对齐(座位、底池、自己的底牌)
//   ③ 同账号在别处登录顶替当前连接时,被顶的一方不会无限重连去抢
//
// 前置:后端在跑(见 docs/dev.md)。

import { expect, test, type Page } from '@playwright/test'
import { FACE_UP_CARD, dropGameSocket, gameSockets, installWsProbe, joinTable, login, waitForTurn } from './helpers'

test.describe('断线重连', () => {
  test('掉线再重连:座位与底牌原样回来,重连后发出的命令服务器仍然接受', async ({ browser }) => {
    const room = `ui-reconnect-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    await ctxA.addInitScript(installWsProbe)
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))

    try {
      await joinTable(a, 'frank', room, 1)
      await joinTable(b, 'carol', room, 2)
      await a.getByRole('button', { name: /Start Game/i }).click()
      await expect(a.locator('text=/In game/i')).toBeVisible({ timeout: 10_000 })

      // 断线前的桌面事实,重连后要一模一样地回来。底牌按图片名比对:牌背是 back.png,滤掉之后
      // 剩下的正面牌 preflop 只可能是自己的两张(别人的底牌在协议上结构性缺位)。
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(2, { timeout: 10_000 })
      const cardsBefore = await a.locator(FACE_UP_CARD).evaluateAll((els) =>
        els.map((e) => (e as HTMLImageElement).getAttribute('src')).sort(),
      )
      const potBefore = await a.getByTestId('pot-amount').innerText()

      // 掉线。重连很快,所以不必担心 ACTION_TIMEOUT=15s 期间服务器替离线的人默认行动。
      const socketsBefore = await dropGameSocket(a)

      // 真的重连上了:又开出一条新连接,而且此刻只有它开着
      await expect
        .poll(async () => (await gameSockets(a)).length, { timeout: 20_000 })
        .toBeGreaterThan(socketsBefore)
      await expect
        .poll(async () => (await gameSockets(a)).filter((s) => s.closeCode === null).length, { timeout: 20_000 })
        .toBe(1)
      await expect(a.locator('text=/正在重连|连接已断开/')).toBeHidden({ timeout: 20_000 })

      // ② 快照重新对齐:还在座、底池不变、自己那两张底牌原样回来
      await expect(a.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
      await expect(a.getByTestId('pot-amount')).toHaveText(potBefore, { timeout: 10_000 })
      await expect(a.locator(FACE_UP_CARD)).toHaveCount(2, { timeout: 10_000 })
      expect(
        await a.locator(FACE_UP_CARD).evaluateAll((els) =>
          els.map((e) => (e as HTMLImageElement).getAttribute('src')).sort(),
        ),
        '重连后的底牌应当与断线前是同两张',
      ).toEqual(cardsBefore)

      // ① seq 没有回退:重连之后 A 自己发出去的命令,服务器照收,B 那边看得见
      expect(await waitForTurn(a, [b]), '重连后应当能等到 A 的回合').toBe(true)
      const potBeforeAct = await b.getByTestId('pot-amount').innerText()
      await a.locator('input[type="number"]').first().fill('20')
      await a.getByRole('button', { name: /^Raise$/ }).click()
      await expect(b.getByTestId('pot-amount')).not.toHaveText(potBeforeAct, { timeout: 10_000 })
      await expect(a.getByTestId('action-error')).toBeHidden()

      // seq 回退的直接症状是服务器判 stale_seq 之后用 4400 关连接。一条都不该有。
      const sockets = await gameSockets(a)
      expect(sockets.filter((s) => s.closeCode === 4400), 'seq 回退会让服务器用 4400 关连接').toEqual([])
      // 也不该是「连上→被拒→再连」的风暴:一次掉线只多出一条连接
      expect(sockets.length, 'A 累计开过的 ws 条数').toBeLessThanOrEqual(socketsBefore + 1)

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test('同账号在别处登录:被顶掉的那一边不再无限重连去抢', async ({ browser }) => {
    const room = `ui-displace-${Date.now().toString(36)}`
    const ctxOld = await browser.newContext()
    const ctxNew = await browser.newContext()
    await ctxOld.addInitScript(installWsProbe)
    await ctxNew.addInitScript(installWsProbe)
    const older = await ctxOld.newPage()
    const newer = await ctxNew.newPage()
    const errors: string[] = []
    older.on('pageerror', (e) => errors.push(`old: ${e}`))
    newer.on('pageerror', (e) => errors.push(`new: ${e}`))

    try {
      // 老连接在牌桌上(顶替最要紧的场景:桌上有筹码)
      await joinTable(older, 'dave', room, 1)
      const socketsBeforeDisplace = (await gameSockets(older)).length

      // 同一个账号在另一个浏览器里重新登录 —— 服务器按 nick 只留一条连接,老的被顶掉
      await login(newer, 'dave')

      // 老连接确实被关了
      await expect
        .poll(async () => (await gameSockets(older)).filter((s) => s.closeCode !== null).length, {
          timeout: 15_000,
        })
        .toBe(socketsBeforeDisplace)

      // 关键:被顶之后不许无限重连去抢。给足 6 秒(退避是 0.5s 起步,乒乓的话早就好几轮了),
      // 期间老连接最多再尝试有限次,而**新连接必须始终活着**——否则两边就是在互相顶。
      await older.waitForTimeout(6_000)
      const oldSockets = await gameSockets(older)
      const newSockets = await gameSockets(newer)
      expect(
        newSockets.filter((s) => s.closeCode !== null).length,
        '新登录那条连接不该被老连接抢回去',
      ).toBe(0)
      expect(
        oldSockets.length,
        `被顶的一方不该反复重连:累计 ${oldSockets.length} 条(顶替前 ${socketsBeforeDisplace} 条)`,
      ).toBeLessThanOrEqual(socketsBeforeDisplace + 1)

      // 而且要让用户知道发生了什么,不能只是一张不动的桌子
      await expect(older.locator('text=/连接已断开|别处登录/')).toBeVisible({ timeout: 10_000 })

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxOld.close()
      await ctxNew.close()
    }
  })
})
