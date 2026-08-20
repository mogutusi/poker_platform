// ws 连接复用的单测。
//
// 钉住的是一条「错了不会报错、只会莫名其妙掉线」的规则:同一 sid 只能有一条 ws。
// 大厅先连上、进牌桌再连一次的话,服务器会把第二条当成「账号在别处登录」而顶掉第一条,
// 表现是刚进牌桌就被踢回登录页——从代码上完全看不出来,所以必须有测试守着。

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConnectionState } from './ws'

// 连接只需要 sessionId 和帧密钥;这里不发真帧,给个空壳即可。
vi.mock('./session', () => ({
  requireSession: () => ({ sessionId: 'sid-1', ws: {} }),
  acceptServerSeq: () => true,
  nextWsSeq: () => 1n,
}))

vi.mock('./config', () => ({ wsUrl: (sid: string) => `ws://test/ws?sid=${sid}` }))

/** 记下每次 new WebSocket,用来断言「到底开了几条」。 */
const opened: FakeWebSocket[] = []

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readyState = FakeWebSocket.CONNECTING
  binaryType = ''
  onopen: (() => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: (() => void) | null = null

  constructor(readonly url: string) {
    opened.push(this)
  }

  /** 模拟握手完成。 */
  accept(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
  }
}

vi.stubGlobal('WebSocket', FakeWebSocket)

const { connect, disconnect, connectionState } = await import('./ws')

beforeEach(() => {
  disconnect()
  opened.length = 0
})

describe('connect', () => {
  it('第一次调用开一条连接', () => {
    connect({ onMessage: () => {} })
    expect(opened).toHaveLength(1)
    expect(opened[0].url).toBe('ws://test/ws?sid=sid-1')
  })

  it('已经连上时再 connect 不开第二条,而是接手现有这条', () => {
    connect({ onMessage: () => {} })
    opened[0].accept()

    connect({ onMessage: () => {} })

    // 开了第二条就等于让服务器把自己顶下线。
    expect(opened).toHaveLength(1)
  })

  it('接手时立刻把 open 状态交给新调用方,否则它的 join_room 永远发不出去', () => {
    connect({ onMessage: () => {} })
    opened[0].accept()

    const states: ConnectionState[] = []
    connect({ onMessage: () => {}, onStateChange: (s) => states.push(s) })

    expect(states).toEqual(['open'])
  })

  it('握手还没完成时再 connect 也复用;稍后的 onopen 通知的是新调用方', () => {
    connect({ onMessage: () => {} })

    const states: ConnectionState[] = []
    connect({ onMessage: () => {}, onStateChange: (s) => states.push(s) })
    expect(opened).toHaveLength(1)
    expect(states).toEqual(['connecting'])

    opened[0].accept()
    expect(states).toEqual(['connecting', 'open'])
  })

  it('断开之后再 connect 才重新开一条', () => {
    connect({ onMessage: () => {} })
    opened[0].accept()
    disconnect()
    expect(connectionState()).toBe('closed')

    connect({ onMessage: () => {} })
    expect(opened).toHaveLength(2)
  })
})
