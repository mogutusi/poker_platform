// ws 加密信道(见 docs/transport.md §二/§三/§四)。
// 出站 seal 成二进制帧,入站按「验 MAC → 解密 → 验 seq → 解 JSON」的铁序处理。
// 组件不直接碰 WebSocket,一律经这里(架构不变量 3)。

import { bytesToUtf8, openFrame, sealFrame, utf8ToBytes } from '@/crypto'
import type { ClientMessage, ServerMessage } from '@/types/wire.gen'
import { wsUrl } from './config'
import { acceptServerSeq, nextWsSeq, requireSession } from './session'

/** 服务器因鉴权/信封问题主动关连接的关闭码;此时应重新登录,不要自动重连。 */
const AUTH_CLOSE_CODE = 4401

const RECONNECT_BASE_MS = 500
const RECONNECT_MAX_MS = 10_000

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ChannelHandlers {
  onMessage: (msg: ServerMessage) => void
  onStateChange?: (state: ConnectionState) => void
  /** 需要用户重新登录(会话失效、被顶替)。不会自动重连。 */
  onAuthLost?: (reason: 'expired' | 'displaced') => void
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
    if (ev.code === AUTH_CLOSE_CODE) {
      // 鉴权问题重连也没用,而且可能是被别处登录顶替了。
      setState('closed')
      handlers?.onAuthLost?.('displaced')
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
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  reconnectAttempt = 0
  socket?.close()
  socket = null
  setState('closed')
}
