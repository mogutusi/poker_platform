// 登出的调用纪律(0097 / BUG-8)。
//
// 「退出」要先让服务器吊销会话,再清本地——只清本地的话服务器上那把 session_token
// 一直有效到 SESSION_TTL 到期。而 handleLogout 里 `logout()` 必须排在 `endSession()`
// **之前**,靠的是 postSealed 同步地读会话、取 seq、封好帧,之后才 await fetch。
// 这条顺序是隐式的:写反了 requireSession() 会抛,被 handleLogout 的 .catch 吞掉,
// 「退出」于是悄悄退回只清本地——正是 0097 要修的那个病,而且一声不响。所以钉住它。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { logout } from './rest'
import { endSession, startSession } from './session'

const TOKEN = new Uint8Array(32).fill(7)

function login() {
  return startSession({ sessionId: 'sid-1', sessionToken: TOKEN, expiresAt: 0, rotateHint: false })
}

/** 拦住 fetch:记下请求体,回一个能被同一会话密钥解开的空响应用不着——只看请求。 */
function stubFetch() {
  const calls: Array<{ url: string; body: { sid: string; frame: string } }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: { body: string }) => {
      calls.push({ url, body: JSON.parse(init.body) })
      // 响应体解不开会抛,但那发生在 await 之后,不影响本测要验的「请求发出去了什么」
      return { ok: true, json: async () => ({ frame: '' }) }
    }),
  )
  return calls
}

beforeEach(() => {
  endSession()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('登出', () => {
  it('打的是 /user/logout,带当前会话的 sid', async () => {
    const calls = stubFetch()
    const session = login()
    await logout().catch(() => undefined)
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('/user/logout')
    expect(calls[0].body.sid).toBe(session.sessionId)
  })

  it('紧跟其后的 endSession() 不影响这次请求:会话与 seq 在 await 之前就已取走', async () => {
    // 这正是 handleLogout 的调用形状。写反顺序的话 requireSession() 会抛,
    // 请求根本发不出去 —— 那时 calls 为空,本测变红。
    const calls = stubFetch()
    const session = login()
    const inFlight = logout().catch(() => undefined)
    endSession() // 立刻清本地,模拟 handleLogout 里紧随其后的那两行
    await inFlight
    expect(calls).toHaveLength(1)
    expect(calls[0].body.sid).toBe(session.sessionId)
    expect(calls[0].body.frame.length).toBeGreaterThan(0) // 帧真的封好了(用的是清空前的密钥)
  })

  it('未登录时不会硬发一个无会话的请求', async () => {
    const calls = stubFetch()
    await expect(logout()).rejects.toThrow()
    expect(calls).toHaveLength(0)
  })
})
