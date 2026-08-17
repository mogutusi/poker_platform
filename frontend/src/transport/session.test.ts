// seq 纪律的测试。这是 docs/transport.md §三 点名的坑:
// seq 按会话计、不按连接计,重连后必须接着往上加;从 0 重来会被服务器判 stale_seq 拒掉,
// 连接被关,然后陷入重连死循环。这条只有代码在保证,所以必须钉一个测试。

import { beforeEach, describe, expect, it } from 'vitest'
import {
  acceptServerSeq,
  endSession,
  getSession,
  nextRestSeq,
  nextWsSeq,
  requireSession,
  startSession,
} from './session'

const TOKEN = new Uint8Array(32).fill(7)

function login() {
  return startSession({ sessionId: 'sid-1', sessionToken: TOKEN, expiresAt: 0, rotateHint: false })
}

beforeEach(() => {
  endSession()
})

describe('会话', () => {
  it('登录后派生出 ws 与 REST 两对密钥,且互不相同', () => {
    const s = login()
    expect(s.ws.encKey).not.toEqual(s.rest.encKey)
    expect(s.ws.macKey).not.toEqual(s.rest.macKey)
    expect(s.ws.encKey).toHaveLength(16)
    expect(s.ws.macKey).toHaveLength(32)
  })

  it('未登录时 requireSession 抛错,getSession 返回 null', () => {
    expect(getSession()).toBeNull()
    expect(() => requireSession()).toThrow()
  })
})

describe('ws 发送 seq', () => {
  it('首帧是 1,之后严格递增', () => {
    login()
    expect(nextWsSeq()).toBe(1n)
    expect(nextWsSeq()).toBe(2n)
    expect(nextWsSeq()).toBe(3n)
  })

  it('重连不重置:seq 按会话计,不按连接计', () => {
    login()
    nextWsSeq()
    nextWsSeq()
    // 这里模拟一次断线重连。重连不碰 session,所以计数器必须接着走。
    expect(nextWsSeq()).toBe(3n)
  })

  it('只有重新登录才归零', () => {
    login()
    nextWsSeq()
    nextWsSeq()
    login()
    expect(nextWsSeq()).toBe(1n)
  })
})

describe('服务器帧 seq 校验', () => {
  it('严格单调:相等或回退都拒', () => {
    login()
    expect(acceptServerSeq(1n)).toBe(true)
    expect(acceptServerSeq(2n)).toBe(true)
    expect(acceptServerSeq(2n)).toBe(false) // 重复
    expect(acceptServerSeq(1n)).toBe(false) // 回退(重放)
    expect(acceptServerSeq(3n)).toBe(true)
  })

  it('拒掉的帧不会把已见上限拉低', () => {
    login()
    acceptServerSeq(5n)
    acceptServerSeq(2n) // 被拒
    expect(acceptServerSeq(5n)).toBe(false)
    expect(acceptServerSeq(6n)).toBe(true)
  })
})

describe('REST seq', () => {
  it('与 ws 各自独立计数', () => {
    login()
    nextWsSeq()
    nextWsSeq()
    expect(nextRestSeq()).toBe(1n)
    expect(nextWsSeq()).toBe(3n)
    expect(nextRestSeq()).toBe(2n)
  })
})
