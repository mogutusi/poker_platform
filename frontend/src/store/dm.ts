// 私聊状态。与房间聊天**不是一回事**,所以不进 room.ts:
// 房聊只在内存留最近 N 条、随房销毁、人人可见、没有未读;私聊点对点、跨房间存在、后端落库、有未读、有已读回执。
// 把两者混在一个 store 里,退出房间时清状态就会顺手把私聊也清掉。
//
// 订阅模型同 room.ts(useSyncExternalStore),状态更新只有两个入口:服务器事件、自己发出的消息。

import type { DMDelivered, DMRead, DMUndelivered } from '@/types/wire.gen'
import { send } from '@/transport/ws'

export interface DMMessage {
  /** 服务器 msg_id;自己发出的还没有(见 sendDm),用 `local-N` 占位,只保证本地唯一。 */
  id: string
  /** true = 我发的,false = 对方发的。 */
  mine: boolean
  text: string
  /** ISO 时间串。对方的消息是服务器墙钟,自己的是本机墙钟(乐观渲染时盖的)。 */
  createdAt: string
  /** 仅自己发的有:对端不存在时服务器回 dm_undelivered,标成投递失败。 */
  undelivered?: boolean
}

export interface DMConversation {
  peer: string
  /** 旧 → 新。 */
  messages: DMMessage[]
  /** 我还没读的对方消息条数。收到即 +1,markRead 归零。 */
  unread: number
  /**
   * 对方读我读到哪(ISO 时间串),null = 还没有回执。
   * 用来显示「已读」,判据见 isReadByPeer。
   */
  peerReadThrough: string | null
}

export interface DmState {
  /** 按对端昵称分组。整个 Map 每次更新都换新实例,读方拿到的是不可变快照。 */
  conversations: Map<string, DMConversation>
}

const EMPTY: DmState = { conversations: new Map() }

let state: DmState = EMPTY
const listeners = new Set<() => void>()

/**
 * 已见过的服务器 msg_id。
 *
 * 登录补收会把离线期的私信重发一遍(服务器只按「读游标」判要不要补,不知道客户端已经收过),
 * 所以归并**必须幂等**,否则每次重连都会把同一批消息再追加一份。
 */
const seenMsgIds = new Set<string>()

let localSeq = 0

function emit(): void {
  for (const l of listeners) l()
}

/** 取会话(不存在就造一个空的),连同「写回新 Map」的动作一起做掉。 */
function upsert(peer: string, patch: (conv: DMConversation) => DMConversation): void {
  const current = state.conversations.get(peer) ?? { peer, messages: [], unread: 0, peerReadThrough: null }
  const next = new Map(state.conversations)
  next.set(peer, patch(current))
  state = { conversations: next }
  emit()
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getDmState(): DmState {
  return state
}

/** 登出/换账号时清空。私聊不随离开房间清,只随会话结束清。 */
export function resetDm(): void {
  state = EMPTY
  seenMsgIds.clear()
  localSeq = 0
  emit()
}

export function getConversation(peer: string, s: DmState = state): DMConversation | null {
  return s.conversations.get(peer) ?? null
}

/** 全部会话,按最后一条消息由新到旧排;没有消息的排最后。用来画会话列表。 */
export function conversationList(s: DmState = state): DMConversation[] {
  return [...s.conversations.values()].sort((a, b) => {
    const at = a.messages[a.messages.length - 1]?.createdAt ?? ''
    const bt = b.messages[b.messages.length - 1]?.createdAt ?? ''
    return bt.localeCompare(at)
  })
}

/** 未读总数,给全局小红点用。 */
export function totalUnread(s: DmState = state): number {
  let n = 0
  for (const c of s.conversations.values()) n += c.unread
  return n
}

/**
 * 这条我发的消息对方是否已读。
 *
 * 回执给的是时间游标(read_through),不是逐条 id,所以只能按时间比。
 * 注意本地乐观渲染盖的是**本机**墙钟,与服务器时钟有偏差时,刚发出的消息可能被判早一拍——
 * 这只影响「已读」小字的显示,不影响任何数据。
 */
export function isReadByPeer(conv: DMConversation, msg: DMMessage): boolean {
  if (!msg.mine || conv.peerReadThrough === null) return false
  return msg.createdAt <= conv.peerReadThrough
}

/**
 * 并进一条私聊相关的服务器消息。room.ts 只负责把这三种转发过来,不做任何 DM 逻辑。
 */
export function applyDmMessage(msg: DMDelivered | DMUndelivered | DMRead): void {
  switch (msg.type) {
    case 'dm_delivered': {
      // 按 msg_id 去重:登录补收会重发离线期的消息,重复的一律丢弃(不追加、也不再计一次未读)。
      if (seenMsgIds.has(msg.msg_id)) return
      seenMsgIds.add(msg.msg_id)
      upsert(msg.from_nick, (conv) => ({
        ...conv,
        messages: [...conv.messages, { id: msg.msg_id, mine: false, text: msg.text, createdAt: msg.created_at }],
        unread: conv.unread + 1,
      }))
      break
    }

    case 'dm_read':
      // 对方读了我发给 ta 的消息。游标只进不退:补收会把回执重发一遍,乱序到达时不能把进度往回带。
      upsert(msg.reader_nick, (conv) => ({
        ...conv,
        peerReadThrough:
          conv.peerReadThrough === null || conv.peerReadThrough < msg.read_through
            ? msg.read_through
            : conv.peerReadThrough,
      }))
      break

    case 'dm_undelivered':
      // 对端不存在。回执只带 to_nick、不带 msg_id,所以只能认「最近一条还没被标失败的自己发的消息」。
      // 对端不存在是个稳定事实,连发多条会一条一条各收到一次回执,依次往前标,不会错位。
      upsert(msg.to_nick, (conv) => {
        const i = findLastIndex(conv.messages, (m) => m.mine && !m.undelivered)
        if (i < 0) return conv
        const messages = [...conv.messages]
        messages[i] = { ...messages[i], undelivered: true }
        return { ...conv, messages }
      })
      break
  }
}

// ── 动作 ──

/**
 * 发一条私信。
 *
 * 成功路径服务器**不回包**(见 service/docs/messaging.md),所以这里本地乐观渲染一条;
 * 真正的送达确认靠对方的已读回执(dm_read)。
 * 失败只有两类会有回包:对端不存在 → dm_undelivered;空/超长/发给自己/限速 → error(由 room.ts 那条通路显示)。
 *
 * ws 没连上时 send 会抛,此时**不**落本地消息——没发出去的东西不该出现在聊天记录里。
 */
export function sendDm(toNick: string, text: string): void {
  send({ type: 'direct_message', to_nick: toNick, text })
  localSeq += 1
  const local: DMMessage = {
    id: `local-${localSeq}`,
    mine: true,
    text,
    createdAt: new Date().toISOString(),
  }
  upsert(toNick, (conv) => ({ ...conv, messages: [...conv.messages, local] }))
}

/**
 * 把与 peer 的会话标为已读。
 *
 * readThrough 是时间游标(ISO 串),缺省取该会话里对方最后一条消息的时间——服务器按 `created_at > 游标`
 * 算未读,所以游标必须是**服务器盖的**那个时间,不能拿本机 Date.now() 顶,否则时钟偏差会把消息误标已读或漏标。
 * 没有可读的消息就什么都不做(不发空命令)。
 */
export function markRead(peerNick: string, readThrough?: string): void {
  const conv = state.conversations.get(peerNick)
  if (!conv) return
  const cursor = readThrough ?? findLast(conv.messages, (m) => !m.mine)?.createdAt
  if (!cursor) return
  send({ type: 'dm_mark_read', peer_nick: peerNick, read_through: cursor })
  upsert(peerNick, (c) => ({ ...c, unread: 0 }))
}

// Array.prototype.findLast(Index) 要 ES2023 的 lib,这里自己写,免得为两个小工具动 tsconfig 目标。
function findLastIndex<T>(arr: readonly T[], pred: (item: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i -= 1) {
    if (pred(arr[i])) return i
  }
  return -1
}

function findLast<T>(arr: readonly T[], pred: (item: T) => boolean): T | undefined {
  const i = findLastIndex(arr, pred)
  return i < 0 ? undefined : arr[i]
}
