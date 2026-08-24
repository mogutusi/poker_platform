// ws 加密信道(见 docs/transport.md §二/§三/§四)。
// 出站 seal 成二进制帧,入站按「验 MAC → 解密 → 验 seq → 解 JSON」的铁序处理。
// 组件不直接碰 WebSocket,一律经这里(架构不变量 3)。

import { bytesToUtf8, openFrame, sealFrame, utf8ToBytes } from '@/crypto'
import type { ClientMessage, ServerMessage } from '@/types/wire.gen'
import { wsUrl } from './config'
import { acceptServerSeq, nextWsSeq, requireSession } from './session'

/** 服务器因鉴权/信封问题主动关连接;会话已经不能用了,应重新登录,不要自动重连。 */
const AUTH_CLOSE_CODE = 4401
/**
 * 同账号的新连接接管了这一条(顶替)。会话本身仍然有效,但这条连接已经让位:
 * **绝不能自动重连**——一重连就把刚上位的那条顶掉,对方再重连,两边无限互顶
 * (0087 在浏览器里实测过 6 秒 6 轮)。见 service/docs/connection.md 顶替语义。
 */
const DISPLACED_CLOSE_CODE = 4409

const RECONNECT_BASE_MS = 500
const RECONNECT_MAX_MS = 10_000

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

/** 连接为什么没了:会话不能用了(须重登)/ 被同账号的新连接接管(别处登录)。 */
export type AuthLostReason = 'expired' | 'displaced'

export interface ChannelHandlers {
  onMessage: (msg: ServerMessage) => void
  onStateChange?: (state: ConnectionState) => void
  /** 连接被服务器终结且不该自动重连(会话失效、被顶替)。 */
  onAuthLost?: (reason: AuthLostReason) => void
}

let socket: WebSocket | null = null
let handlers: ChannelHandlers | null = null
let state: ConnectionState = 'idle'
let reconnectAttempt = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
/** true 表示是我们自己要求断开的,不该触发重连。 */
let closedByUs = false

function setState(next: ConnectionState): void {
  if (state === next) return
  state = next
  handlers?.onStateChange?.(next)
}

export function connectionState(): ConnectionState {
  return state
}

/** 连接 ws。同一会话重连时 seq 继续累加(在 session 模块里维护),不要在这里归零。 */
export function connect(h: ChannelHandlers): void {
  handlers = h
  closedByUs = false

  // 已经有一条活着的连接就接手它,绝不开第二条:同一 sid 的第二条 ws 会被服务器当成
  // 「账号在别处登录」而把前一条静默顶掉(见 docs/transport.md §四)。大厅先连着、
  // 进牌桌再连一次的话,等于自己把自己踢下线。
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    // 复用不会再触发 onopen,得手动把当前状态交给接手的调用方,
    // 否则它挂在 onStateChange('open') 里的 join_room 永远发不出去。
    h.onStateChange?.(state)
    return
  }

  open()
}

function open(): void {
  const session = requireSession()
  setState(reconnectAttempt === 0 ? 'connecting' : 'reconnecting')

  const ws = new WebSocket(wsUrl(session.sessionId))
  ws.binaryType = 'arraybuffer'
  socket = ws

  ws.onopen = () => {
    reconnectAttempt = 0
    setState('open')
  }

  ws.onmessage = (ev) => {
    if (!(ev.data instanceof ArrayBuffer)) return // 加密信道只走二进制帧,文本帧一律忽略
    let msg: ServerMessage
    try {
      const opened = openFrame(session.ws, new Uint8Array(ev.data))
      // seq 必须严格递增。不新鲜的帧是重放或乱序,直接丢。
      if (!acceptServerSeq(opened.seq)) return
      msg = JSON.parse(bytesToUtf8(opened.plaintext)) as ServerMessage
    } catch {
      // 拆帧失败(MAC 不符/解密坏/JSON 坏)只丢这一帧,不猜原因、不重连。
      return
    }
    handlers?.onMessage(msg)
  }

  ws.onclose = (ev) => {
    socket = null
    if (closedByUs) {
      setState('closed')
      return
    }
    if (ev.code === AUTH_CLOSE_CODE || ev.code === DISPLACED_CLOSE_CODE) {
      // 两种都不该自动重连:鉴权问题重连也没用;被顶替则重连等于去抢,会变成两边互顶。
      setState('closed')
      handlers?.onAuthLost?.(ev.code === DISPLACED_CLOSE_CODE ? 'displaced' : 'expired')
      return
    }
    scheduleReconnect()
  }

  ws.onerror = () => {
    // 错误之后必然跟一个 close,重连逻辑统一放在 onclose,这里不重复处理。
  }
}

function scheduleReconnect(): void {
  setState('reconnecting')
  const delay = Math.min(RECONNECT_BASE_MS * 2 ** reconnectAttempt, RECONNECT_MAX_MS)
  reconnectAttempt += 1
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    try {
      open()
    } catch {
      // 会话已经没了(比如用户登出),不再重试。
      setState('closed')
    }
  }, delay)
}

/** 发一条命令。身份不进报文,服务器按连接绑定的会话认人。 */
export function send(msg: ClientMessage): void {
  const session = requireSession()
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error('ws not connected')
  }
  const frame = sealFrame(session.ws, nextWsSeq(), utf8ToBytes(JSON.stringify(msg)))
  // sealFrame 返回的是恰好长度的 Uint8Array,可直接当帧发。
  socket.send(frame)
}

export function disconnect(): void {
  closedByUs = true
  // 连同回调一起清掉:组件卸载后,还没触发的 onopen/onclose 不该再回调到已经走人的调用方,
  // 否则它会在一条已经关掉的连接上 send,抛 "ws not connected"。
  handlers = null
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  reconnectAttempt = 0
  socket?.close()
  socket = null
  setState('closed')
}
