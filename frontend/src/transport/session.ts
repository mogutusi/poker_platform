// 会话与秘密的存放处。分级见 docs/transport.md §六:
//   K_user        → localStorage(每周轮换,要跨会话留存,否则每次登录都要手输)
//   session_token → 只在内存(能派生所有帧密钥,泄露等于会话被接管;刷新页面即重登)
//   seq           → 按会话计,跨 ws 重连继续累加;只有重新登录才归零

import { type ChannelKeys, deriveRestKeys, deriveWsKeys, hexToBytes } from '@/crypto'

const K_USER_STORAGE_KEY = 'poker.k_user'

/** 一次登录换回的会话。token 是秘密,只活在内存里。 */
export interface Session {
  sessionId: string
  sessionToken: Uint8Array
  expiresAt: number
  /** true 表示服务器是用旧钥认出你的,应尽快找管理员换新 K_user。 */
  rotateHint: boolean
  ws: ChannelKeys
  rest: ChannelKeys
}

let current: Session | null = null

/**
 * ws 发送序号。按会话计、不按连接计:重连后必须接着往上加,
 * 从 0 重来会被服务器判 stale_seq 拒掉,然后陷入重连死循环。
 */
let wsSendSeq = 0n
/** 已见过的服务器帧最大 seq,用于严格单调校验。 */
let wsSeenSeq = 0n
/** REST 请求序号,与 ws 各自独立(密钥已分域,seq 空间天然分开)。 */
let restSeq = 0n

export function startSession(params: {
  sessionId: string
  sessionToken: Uint8Array
  expiresAt: number
  rotateHint: boolean
}): Session {
  current = {
    sessionId: params.sessionId,
    sessionToken: params.sessionToken,
    expiresAt: params.expiresAt,
    rotateHint: params.rotateHint,
    ws: deriveWsKeys(params.sessionToken),
    rest: deriveRestKeys(params.sessionToken),
  }
  wsSendSeq = 0n
  wsSeenSeq = 0n
  restSeq = 0n
  return current
}

export function getSession(): Session | null {
  return current
}

export function requireSession(): Session {
  if (!current) throw new Error('not logged in')
  return current
}

export function endSession(): void {
  current = null
  wsSendSeq = 0n
  wsSeenSeq = 0n
  restSeq = 0n
}

/** 取下一个 ws 发送序号。首帧是 1。 */
export function nextWsSeq(): bigint {
  wsSendSeq += 1n
  return wsSendSeq
}

/** 校验服务器帧的 seq 严格递增;不新鲜就拒(重放或乱序)。 */
export function acceptServerSeq(seq: bigint): boolean {
  if (seq <= wsSeenSeq) return false
  wsSeenSeq = seq
  return true
}

/** 取下一个 REST 序号。重试必须重封新 seq,原样重发会被判重放。 */
export function nextRestSeq(): bigint {
  restSeq += 1n
  return restSeq
}

// ── K_user:带外发放的每用户共享密钥,16 字节 ──

export function loadKUser(): Uint8Array | null {
  if (typeof window === 'undefined') return null
  const hex = window.localStorage.getItem(K_USER_STORAGE_KEY)
  if (!hex) return null
  try {
    const bytes = hexToBytes(hex)
    return bytes.length === 16 ? bytes : null
  } catch {
    return null
  }
}

export function saveKUser(hex: string): void {
  const bytes = hexToBytes(hex.trim())
  if (bytes.length !== 16) throw new Error('K_user 必须是 16 字节(32 个十六进制字符)')
  window.localStorage.setItem(K_USER_STORAGE_KEY, hex.trim().toLowerCase())
}

export function clearKUser(): void {
  if (typeof window !== 'undefined') window.localStorage.removeItem(K_USER_STORAGE_KEY)
}

export function hasKUser(): boolean {
  return loadKUser() !== null
}
