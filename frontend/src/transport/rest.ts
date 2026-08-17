// REST:公开读是明文 GET,需身份的端点走加密信封(见 docs/transport.md §五 / service/docs/rest.md)。
// 牌局操作一律走 ws,不在这里。

import { bytesToHex, bytesToUtf8, hexToBytes, openFrame, sealFrame, utf8ToBytes } from '@/crypto'
import { API_BASE_URL } from './config'
import { nextRestSeq, requireSession } from './session'

// ── 公开读(明文,无需登录)──

/** 大厅房间列表。只有汇总信息,逐座位的详情要 join_room 之后由 StateSnapshot 带来。 */
export interface RoomMeta {
  id: string
  small_blind: number
  big_blind: number
  buy_in: number
  max_seats: number
  seated: number
  watching: number
  status: 'pending_start' | 'hand_started'
}

export interface LeaderboardEntry {
  rank: number
  nickname: string
  points: number
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`)
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`)
  return (await res.json()) as T
}

export function fetchRooms(): Promise<RoomMeta[]> {
  return getJson<RoomMeta[]>('/lobby/rooms')
}

export function fetchLeaderboard(limit?: number): Promise<LeaderboardEntry[]> {
  return getJson<LeaderboardEntry[]>(`/leaderboard${limit ? `?limit=${limit}` : ''}`)
}

// ── 需身份的端点(加密信封)──

/**
 * 发一个加密信封请求。响应回显请求的 seq,服务器用滑动窗判重。
 *
 * 重试必须重新调用本函数(会拿到新 seq);把同一个 frame 原样重发一定被判成重放。
 */
export async function postSealed<T>(path: string, payload: unknown): Promise<T> {
  const session = requireSession()
  const frame = sealFrame(session.rest, nextRestSeq(), utf8ToBytes(JSON.stringify(payload)))

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid: session.sessionId, frame: bytesToHex(frame) }),
  })
  if (!res.ok) throw new RestError(`POST ${path} failed`, res.status)

  const body = (await res.json()) as { frame: string }
  const opened = openFrame(session.rest, hexToBytes(body.frame))
  return JSON.parse(bytesToUtf8(opened.plaintext)) as T
}

export class RestError extends Error {
  constructor(
    message: string,
    /** 401 信封不过 · 403/409/400 业务错 · 500 服务端故障(见 service/docs/rest.md) */
    readonly status: number,
  ) {
    super(message)
  }
}

export interface Profile {
  name: string
  nickname: string
  points: number
}

export function fetchProfile(): Promise<Profile> {
  return postSealed<Profile>('/user/me', {})
}
