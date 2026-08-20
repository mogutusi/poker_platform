// 私聊归并的单测。重点钉住那些「错了会静默出错、界面上又不容易发现」的规则:
// 补收重发必须幂等、未读的加减、投递失败的标记。

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClientMessage } from '@/types/wire.gen'

// dm.ts 经 ws 发命令。这里只关心「发了什么」,不连真连接。
const sent: ClientMessage[] = []
vi.mock('@/transport/ws', () => ({
  send: (msg: ClientMessage) => {
    sent.push(msg)
  },
}))

import {
  applyDmMessage,
  conversationList,
  getConversation,
  getDmState,
  isReadByPeer,
  markRead,
  resetDm,
  sendDm,
  totalUnread,
} from './dm'
import { applyServerMessage } from './room'

/** 一条对方发来的私信。默认 bob 发的。 */
function delivered(msgId: string, over: { from?: string; text?: string; at?: string } = {}) {
  return {
    type: 'dm_delivered',
    msg_id: msgId,
    from_nick: over.from ?? 'bob',
    text: over.text ?? 'hi',
    created_at: over.at ?? '2026-01-01T00:00:00Z',
  } as const
}

beforeEach(() => {
  resetDm()
  sent.length = 0
})

describe('收私信', () => {
  it('按对端昵称分组,各自独立计未读', () => {
    applyDmMessage(delivered('m1', { from: 'bob', text: '在吗' }))
    applyDmMessage(delivered('m2', { from: 'carol', text: '开局了' }))
    applyDmMessage(delivered('m3', { from: 'bob', text: '打牌吗' }))

    expect(getConversation('bob')?.messages.map((m) => m.text)).toEqual(['在吗', '打牌吗'])
    expect(getConversation('bob')?.unread).toBe(2)
    expect(getConversation('carol')?.unread).toBe(1)
    expect(totalUnread()).toBe(3)
  })

  it('按 msg_id 去重:登录补收重发同一批消息不产生重复,也不重复计未读', () => {
    applyDmMessage(delivered('m1'))
    applyDmMessage(delivered('m2', { at: '2026-01-01T00:00:01Z' }))
    // 重连补收:服务器按「读游标」补,不知道客户端已经收过,同样两条会再来一遍。
    applyDmMessage(delivered('m1'))
    applyDmMessage(delivered('m2', { at: '2026-01-01T00:00:01Z' }))

    expect(getConversation('bob')?.messages).toHaveLength(2)
    expect(getConversation('bob')?.unread).toBe(2)
  })

  it('收到的消息标成对方发的,不是自己发的', () => {
    applyDmMessage(delivered('m1'))
    expect(getConversation('bob')?.messages[0].mine).toBe(false)
  })
})

describe('未读', () => {
  it('markRead 后归零,并把对方最后一条的时间当游标发出去', () => {
    applyDmMessage(delivered('m1', { at: '2026-01-01T00:00:00Z' }))
    applyDmMessage(delivered('m2', { at: '2026-01-01T00:00:09Z' }))
    expect(getConversation('bob')?.unread).toBe(2)

    markRead('bob')

    expect(getConversation('bob')?.unread).toBe(0)
    expect(totalUnread()).toBe(0)
    expect(sent).toEqual([
      { type: 'dm_mark_read', peer_nick: 'bob', read_through: '2026-01-01T00:00:09Z' },
    ])
  })

  it('游标必须是服务器盖的时间,不能用本机时钟:只认对方消息的 created_at', () => {
    applyDmMessage(delivered('m1', { at: '2026-01-01T00:00:00Z' }))
    sendDm('bob', '我回一句') // 自己发的带本机时间,不能被当成已读游标
    markRead('bob')
    expect(sent.at(-1)).toEqual({
      type: 'dm_mark_read',
      peer_nick: 'bob',
      read_through: '2026-01-01T00:00:00Z',
    })
  })

  it('没有可读的消息就不发空命令', () => {
    markRead('bob') // 会话都不存在
    sendDm('bob', '只有我发的')
    sent.length = 0
    markRead('bob') // 会话里只有自己发的
    expect(sent).toEqual([])
  })

  it('标记已读之后又来新消息,未读重新累计', () => {
    applyDmMessage(delivered('m1'))
    markRead('bob')
    applyDmMessage(delivered('m2', { at: '2026-01-01T00:00:05Z' }))
    expect(getConversation('bob')?.unread).toBe(1)
  })
})

describe('发私信', () => {
  it('成功路径服务器不回包,所以本地乐观渲染一条,并且不计自己的未读', () => {
    sendDm('bob', '来一局')
    expect(sent).toEqual([{ type: 'direct_message', to_nick: 'bob', text: '来一局' }])
    const conv = getConversation('bob')
    expect(conv?.messages).toHaveLength(1)
    expect(conv?.messages[0]).toMatchObject({ mine: true, text: '来一局' })
    expect(conv?.unread).toBe(0)
  })

  it('dm_undelivered:对端不存在,把最近一条自己发的标成投递失败', () => {
    sendDm('ghost', '有人吗')
    applyDmMessage({ type: 'dm_undelivered', to_nick: 'ghost' })
    expect(getConversation('ghost')?.messages[0].undelivered).toBe(true)
  })

  it('连发多条各收一次回执,依次往前标,不会重复标同一条', () => {
    sendDm('ghost', '一')
    sendDm('ghost', '二')
    applyDmMessage({ type: 'dm_undelivered', to_nick: 'ghost' })
    applyDmMessage({ type: 'dm_undelivered', to_nick: 'ghost' })
    expect(getConversation('ghost')?.messages.map((m) => m.undelivered)).toEqual([true, true])
  })

  it('对方发来的消息不会被投递失败回执误标', () => {
    applyDmMessage(delivered('m1', { from: 'bob' }))
    applyDmMessage({ type: 'dm_undelivered', to_nick: 'bob' })
    expect(getConversation('bob')?.messages[0].undelivered).toBeUndefined()
  })
})

describe('已读回执', () => {
  it('dm_read 记下对方读到哪,据此判断我发的哪几条已读', () => {
    applyDmMessage({ type: 'dm_read', reader_nick: 'bob', read_through: '2026-01-01T00:00:05Z' })
    const conv = getConversation('bob')!
    expect(conv.peerReadThrough).toBe('2026-01-01T00:00:05Z')
    expect(isReadByPeer(conv, { id: 'a', mine: true, text: 'x', createdAt: '2026-01-01T00:00:01Z' })).toBe(true)
    expect(isReadByPeer(conv, { id: 'b', mine: true, text: 'y', createdAt: '2026-01-01T00:00:09Z' })).toBe(false)
    // 对方发来的消息没有「对方已读」这回事
    expect(isReadByPeer(conv, { id: 'c', mine: false, text: 'z', createdAt: '2026-01-01T00:00:01Z' })).toBe(false)
  })

  it('游标只进不退:补收重发的旧回执不能把进度带回去', () => {
    applyDmMessage({ type: 'dm_read', reader_nick: 'bob', read_through: '2026-01-01T00:00:09Z' })
    applyDmMessage({ type: 'dm_read', reader_nick: 'bob', read_through: '2026-01-01T00:00:01Z' })
    expect(getConversation('bob')?.peerReadThrough).toBe('2026-01-01T00:00:09Z')
  })
})

describe('会话列表', () => {
  it('按最后一条消息由新到旧排', () => {
    applyDmMessage(delivered('m1', { from: 'bob', at: '2026-01-01T00:00:00Z' }))
    applyDmMessage(delivered('m2', { from: 'carol', at: '2026-01-01T00:00:09Z' }))
    expect(conversationList().map((c) => c.peer)).toEqual(['carol', 'bob'])
  })
})

describe('快照不可变', () => {
  it('每次更新都换一个新的 conversations 实例(useSyncExternalStore 靠引用判变化)', () => {
    const before = getDmState().conversations
    applyDmMessage(delivered('m1'))
    expect(getDmState().conversations).not.toBe(before)
  })
})

describe('room.ts 只做转发', () => {
  it('DM 事件经 applyServerMessage 进来也落到私聊 store', () => {
    applyServerMessage(delivered('m1'))
    expect(getConversation('bob')?.messages).toHaveLength(1)
    expect(getConversation('bob')?.unread).toBe(1)
  })
})
