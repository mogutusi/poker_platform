// 牌桌聊天(房聊):两个人在同一桌,一个人发、另一个人真的收到;表情 token 按目录渲染。
//
// 房聊协议、防护、历史早就在了,0110 记的缺口是「界面一个字没渲染」;这条用例守的就是那个界面。
// 账号:dave / frank,只观战不入座,不留残局。前置:后端在跑。

import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('牌桌聊天', () => {
  test('一个人发,同桌另一个人收到;[thumbs_up] 渲染成 👍', async ({ browser }) => {
    const room = `ui-chat-${Date.now().toString(36)}`
    const ctxA = await browser.newContext()
    const ctxB = await browser.newContext()
    const a = await ctxA.newPage()
    const b = await ctxB.newPage()
    const errors: string[] = []
    a.on('pageerror', (e) => errors.push(`A: ${e}`))
    b.on('pageerror', (e) => errors.push(`B: ${e}`))
    try {
      for (const [p, user] of [[a, 'dave'], [b, 'frank']] as const) {
        await login(p, user)
        await p.getByLabel('房间名').fill(room)
        await p.getByTestId('enter-room').click()
        await expect(p.locator('text=/观战中/')).toBeVisible({ timeout: 15_000 })
      }

      const line = `nice hand ${Date.now().toString(36)}`
      await a.getByLabel('聊天输入').fill(`${line} [thumbs_up]`)
      await a.getByLabel('聊天输入').press('Enter')

      const chatB = b.getByRole('region', { name: '牌桌聊天' })
      await expect(chatB.getByText(line)).toBeVisible({ timeout: 10_000 })
      await expect(chatB.getByText('dave')).toBeVisible()
      await expect(chatB.getByText('👍')).toBeVisible()
      // 发件人自己也看到这一行(服务器广播给全房,含发送者)
      await expect(a.getByRole('region', { name: '牌桌聊天' }).getByText(line)).toBeVisible()

      expect(errors, `页面抛出未捕获错误:\n${errors.join('\n')}`).toEqual([])
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })
})
