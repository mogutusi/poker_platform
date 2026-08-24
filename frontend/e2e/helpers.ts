// 浏览器用例共用的脚手架:登录、进房入座、推进手牌。
//
// 抽出来的理由与 scripts/smoke-client.mjs 同款(0085):同一套 30 行已经在 4 个 spec 里各抄一份,
// 界面一改就得改四处,漏一处就是一个静默失效的用例。更实在的教训在 `advanceHand` 上——
// 各 spec 自己写的推进循环**先点 Check 再点 Call**,而按钮并不按规则灰(合法与否由服务器裁定,
// 见 docs/architecture.md 前端不变量 1),于是 heads-up preflop 里小盲那一下 Check 必被
// ILLEGAL_ACTION 拒,循环空转到 ACTION_TIMEOUT 才靠「超时自动弃牌」收场——用例照样绿,
// 但它自称验过的摊牌一次都没走到(0087 实测)。推进逻辑只留这一份,并且**认服务器的拒绝**。

import { expect, type Page } from '@playwright/test'

/** dev 种子凭据,仅限本地(见 service/app/poker.env.example)。 */
export const K_USER = '00112233445566778899aabbccddeeff'
export const PASSWORD = 'devpass123'

/** 正面朝上的牌(牌背是 back.png):自己的底牌与公共牌走这个选择器。 */
export const FACE_UP_CARD = 'img[src*="/cards/"]:not([src*="back.png"])'

export async function login(page: Page, user: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel(/Username/i).fill(user)
  await page.getByLabel(/Password/i).fill(PASSWORD)
  await page.getByLabel(/K_user/i).fill(K_USER)
  await page.locator('button[type="submit"]').click()
  await expect(page).toHaveURL(/\/lobby/, { timeout: 15_000 })
}

/** 登录 → 进房 → 入座 → 买入 → 准备。座位号是界面上的 1 起编号。 */
export async function joinTable(page: Page, user: string, room: string, seat: number): Promise<void> {
  await login(page, user)
  await page.getByLabel('房间名').fill(room)
  await page.getByTestId('enter-room').click()
  await expect(page).toHaveURL(/\/game/, { timeout: 10_000 })
  await page.locator(`[data-empty-seat="${seat}"]`).click()
  await expect(page.locator('text=/观战中/')).toBeHidden({ timeout: 10_000 })
  await page.getByRole('button', { name: '买入' }).click()
  const ready = page.getByRole('button', { name: /^Ready$/ })
  await expect(ready).toBeEnabled({ timeout: 10_000 })
  await ready.click()
}

/** 现在轮到哪一页行动。按钮只在自己回合可用,所以「谁能点谁就是行动者」。 */
export async function whoActs(pages: Page[]): Promise<Page | null> {
  for (const p of pages) {
    const fold = p.getByRole('button', { name: /^Fold$/ })
    if ((await fold.isVisible().catch(() => false)) && (await fold.isEnabled().catch(() => false))) return p
  }
  return null
}

/** 把界面上挂着的服务器错误提示点掉(它是一个按钮,点一下就消)。没有就什么都不做。 */
export async function dismissError(page: Page): Promise<void> {
  const err = page.getByTestId('action-error')
  if (await err.isVisible().catch(() => false)) {
    await err.click()
    await expect(err).toBeHidden({ timeout: 5_000 })
  }
}

/**
 * 桌面指纹:行动权在不在我这、正面朝上的牌几张、底池多少。用来判断「我点的这一下服务器收了没」。
 *
 * 为什么不能只看「行动权是不是移走了」:heads-up 里同一个人可以连着行动两次——preflop 最后
 * 一下 check 关掉本街,翻牌圈又轮到同一个人(postflop 由非庄先说话)。那时行动权没变,变的是牌。
 */
async function fingerprint(page: Page): Promise<string> {
  // 一次 evaluate 取完,**不用 locator 的 isEnabled/textContent**:那些会自动等元素出现,
  // 而手牌一结束整条行动栏和底池就从 DOM 上消失,于是它们不是「返回没有」而是**一直等到超时**,
  // 指纹永远算不出来,轮询只好判成 pending(0087 在这上面卡过一轮)。
  return page.evaluate(() => {
    const fold = Array.from(document.querySelectorAll('button')).find(
      (b) => (b.textContent || '').trim() === 'Fold',
    ) as HTMLButtonElement | undefined
    return JSON.stringify([
      fold ? !fold.disabled : null,
      document.querySelectorAll('img[src*="/cards/"]:not([src*="back.png"])').length,
      document.querySelector('[data-testid="pot-amount"]')?.textContent ?? null,
    ])
  })
}

/**
 * 点一个行动按钮,并**等服务器表态**:要么接受(桌面动了),要么拒绝(界面弹出 ErrorCode 提示)。
 * 返回哪一种。
 *
 * 必须等到表态才能返回:早期的用例点完就往下走,于是「点了但被拒」和「点了且生效」分不出来,
 * 循环空转到 ACTION_TIMEOUT 才由服务器替人默认行动收场,用例照样绿(0087 实测)。
 */
export async function actAndWait(page: Page, name: RegExp): Promise<'accepted' | 'rejected'> {
  const err = page.getByTestId('action-error')
  const before = await fingerprint(page)
  await page.getByRole('button', { name }).click()
  await expect
    .poll(
      async () => {
        if (await err.isVisible().catch(() => false)) return 'rejected'
        return (await fingerprint(page)) === before ? 'pending' : 'accepted'
      },
      { timeout: 10_000 },
    )
    .not.toBe('pending')
  return (await err.isVisible().catch(() => false)) ? 'rejected' : 'accepted'
}

/**
 * 让 page 走一步「不加注地跟上」:先试 Check,服务器拒了再 Call。
 *
 * **不能只点 Check**:Check / Call 哪个合法是规则,前端按钮不预判(不变量 1),所以点错很正常,
 * 判据只能是服务器的回答。两个都被拒就直接抛——那说明用例的假设错了,应当红,不该继续空转。
 */
export async function checkOrCall(page: Page): Promise<void> {
  await dismissError(page)
  if ((await actAndWait(page, /^Check$/)) === 'accepted') return
  await dismissError(page)
  if ((await actAndWait(page, /^Call/)) === 'rejected') {
    const detail = await page.getByTestId('action-error').getAttribute('title')
    throw new Error(`Check 与 Call 都被服务器拒了:${detail}`)
  }
}

/** 一直让别人 check/call,直到轮到 target。返回是否等到了。 */
export async function waitForTurn(target: Page, others: Page[], steps = 6): Promise<boolean> {
  for (let i = 0; i < steps; i++) {
    const actor = await whoActs([target, ...others])
    if (actor === target) return true
    if (actor === null) {
      await target.waitForTimeout(300)
      continue
    }
    await checkOrCall(actor)
  }
  return (await whoActs([target, ...others])) === target
}

/**
 * 一直让能行动的人 check/call,直到 `until` 为真或步数用完。返回是否等到了。
 *
 * 每一步都真的推进了牌局(见 checkOrCall),所以「步数用完」是真的走不动,不是空转。
 */
export async function advanceHand(
  pages: Page[],
  until: () => Promise<boolean>,
  steps = 30,
): Promise<boolean> {
  for (let i = 0; i < steps; i++) {
    if (await until()) return true
    const actor = await whoActs(pages)
    if (actor === null) {
      await pages[0].waitForTimeout(200)
      continue
    }
    await checkOrCall(actor)
  }
  return until()
}

/**
 * 记录本页每一条 ws 的开合与**关闭码**。
 *
 * 为什么要自己包一层:Playwright 的 `page.on('websocket')` 只给「关了」,不给关闭码,而本篇
 * 要断言的恰恰是关闭码(4400 = 服务器判 seq 回退;4401 = 鉴权/会话;1005 = 没带码的普通关闭)。
 * 这是**测试侧**的仪器,包的是浏览器自己的 WebSocket API,不往生产代码里塞测试钩子。
 */
export function installWsProbe(): void {
  const w = window as unknown as {
    WebSocket: typeof WebSocket
    __wsLog: { url: string; closeCode: number | null }[]
    __wsSockets: WebSocket[]
  }
  w.__wsLog = []
  w.__wsSockets = []
  const Native = w.WebSocket
  class Probed extends Native {
    constructor(url: string | URL, protocols?: string | string[]) {
      super(url, protocols)
      const entry = { url: String(url), closeCode: null as number | null }
      w.__wsLog.push(entry)
      w.__wsSockets.push(this)
      this.addEventListener('close', (ev) => {
        entry.closeCode = (ev as CloseEvent).code
      })
    }
  }
  w.WebSocket = Probed as unknown as typeof WebSocket
}

/**
 * 只看牌局那条 ws。两处噪声必须滤掉:`next dev` 自己的 HMR ws,以及 React 严格模式下
 * 开发期 effect 跑两遍带来的**第一条随即被关掉的连接**——所以这里数的是「累计开过几条」,
 * 断言一律用**增量**,不用绝对值。
 */
export async function gameSockets(page: Page): Promise<{ url: string; closeCode: number | null }[]> {
  const log = await page.evaluate(
    () => (window as unknown as { __wsLog?: { url: string; closeCode: number | null }[] }).__wsLog ?? [],
  )
  return log.filter((s) => s.url.includes('/ws?sid='))
}

/**
 * 把牌局那条 ws 从**客户端**掐掉,模拟掉线;返回掐之前已经开过几条(用来判断重连真的发生了)。
 *
 * 不用 `context.setOffline(true)`:实测它只挡新请求,**已建立的 WebSocket 照常活着**
 * (2026-08-24 在 chromium 上验过,断线横幅根本不出现)。所以改成直接关那条 socket ——
 * 前端看到的是「不是我要求的关闭」,与真掉线走同一条重连分支(见 transport/ws.ts onclose)。
 */
export async function dropGameSocket(page: Page): Promise<number> {
  const before = (await gameSockets(page)).length
  await page.evaluate(() => {
    const list = (window as unknown as { __wsSockets: WebSocket[] }).__wsSockets
    for (const s of list) {
      if (s.url.includes('/ws?sid=') && s.readyState === WebSocket.OPEN) s.close()
    }
  })
  return before
}
