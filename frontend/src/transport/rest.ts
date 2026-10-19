// REST:**每一个端点都走加密信封**,登录是唯一暴露在外的入口(见 docs/transport.md §五 /
// service/docs/auth.md §加密信道)。0094 之前 lobby/leaderboard/hands 是明文 GET,那是 P5 落地前的
// 残留——没有 TLS 的前提下,明文读等于把房间、排行、逐手财务流水裸露在内网上。
// 牌局操作一律走 ws,不在这里。

import { bytesToHex, bytesToUtf8, hexToBytes, openFrame, sealFrame, utf8ToBytes } from '@/crypto'
import type { RoomStatus } from '@/types/wire.gen'
import { API_BASE_URL } from './config'
import { nextRestSeq, requireSession } from './session'

// ── 读接口(同样走信封,需已登录)──

/** 大厅桌面预览的一个已占座位(公开信息:谁坐哪、桌上多少筹码)。 */
export interface LobbySeat {
  seat_position: number
  nickname: string
  points: number
}

/** 大厅房间列表:汇总信息 + 已占座位(预览用)。底牌、行动等牌局细节要 join_room 之后由 StateSnapshot 带来。 */
export interface RoomMeta {
  id: string
  small_blind: number
  big_blind: number
  buy_in: number
  max_seats: number
  seated: number
  watching: number
  status: RoomStatus  // 取 codegen 产物,不手抄字面量:抄一份就是第二处会漂的协议事实源(0099)
  seats: LobbySeat[]
}

export interface LeaderboardEntry {
  rank: number
  nickname: string
  points: number
}

/**
 * 一手已结束牌局的历史条目(service/app/rest/hands.py `HandRecordView`)。
 * 底牌只给你自己的,和摊牌时亮出来的;别人没亮的牌服务器不会发(见 service/docs/rest.md)。
 */
export interface HandRecord {
  /** 自增主键,兼作下一页游标(下一页传 before=本页最后一条的 id)。 */
  id: number
  /** 手牌标识,形如 `"房名:序号"`;房名要用 handRoom() 取,别自己 split。 */
  dedupe_key: string
  /** ISO 时间串,由后端墙钟盖。 */
  start_time: string
  end_time: string
  /** 各子池之和,不含退还的未叫注。 */
  final_pot: number
  /** 该手全部参与者,按昵称升序;net = final_points - initial_points。 */
  participants: HandParticipant[]
  /** 公共牌,如 ["Ah","7c","2d"];翻牌前结束为空。较早(没记明细)的手也为空。 */
  board: string[]
  /** 庄位座位号(0 起);较早的手为 null。 */
  button_position: number | null
  big_blind: number | null
}

export interface HandParticipant {
  nickname: string
  initial_points: number
  final_points: number
  net: number
  /** 座位号(0 起);较早的手为 null。 */
  seat_position: number | null
  /** 底牌,如 ["As","Kd"]:只有自己的,或摊牌亮过的才有,其余为 null。 */
  hole_cards: string[] | null
  /** 摊牌时亮了牌。 */
  showed: boolean
}

export interface Rival {
  nickname: string
  amount: number
}

/** POST /leaderboard/stats 的一行(service/app/rest/leaderboard.py `StatsEntry`)。 */
export interface StatsEntry {
  rank: number
  nickname: string
  points: number
  hands: number
  net: number
  won_hands: number
  /** 0..1 */
  win_rate: number
  /** 0..1;只有老手牌(没记明细)时为 null。 */
  vpip: number | null
  bb_per_100: number | null
  most_won_from: Rival | null
  most_lost_to: Rival | null
}

export interface HandsQuery {
  /** 按参与者昵称过滤;此人没打过任何一手(或根本不存在)时返回空数组。 */
  user?: string
  /** 按房名精确过滤。 */
  room?: string
  /** 游标:只取 id 严格小于它的记录,即「更旧的一页」。 */
  before?: number
  limit?: number
}

/** 去掉值为 undefined 的键:信封内层是 JSON,`{"limit": undefined}` 序列化后那个键会消失,但显式列出更清楚。 */
function defined(params: Record<string, string | number | undefined>): Record<string, string | number> {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined)) as Record<
    string,
    string | number
  >
}

export async function fetchRooms(): Promise<RoomMeta[]> {
  return (await postSealed<{ rooms: RoomMeta[] }>('/lobby/rooms', {})).rooms
}

export async function fetchLeaderboard(limit?: number): Promise<LeaderboardEntry[]> {
  return (await postSealed<{ entries: LeaderboardEntry[] }>('/leaderboard', defined({ limit }))).entries
}

/** 统计榜,按净赢降序。`since` = epoch 秒(只算这之后结束的手);不传 = 全部时间。 */
export async function fetchLeaderboardStats(q: { since?: number; limit?: number } = {}): Promise<StatsEntry[]> {
  return (await postSealed<{ entries: StatsEntry[] }>('/leaderboard/stats', defined({ since: q.since, limit: q.limit })))
    .entries
}

/** 一个比率:做了几次 / 有几次机会;没机会时 pct 为 null。 */
export interface Ratio {
  made: number
  chances: number
  pct: number | null
}

/** POST /stats/me(service/app/rest/stats.py `PlayerStats`)。只有自己的数据。 */
export interface PlayerStats {
  nickname: string
  /** 后端本地测试开关 DEV_STATS_ANY_USER 开着时为 true:可以切换看别的玩家(含 AI)。 */
  can_view_others: boolean
  summary: {
    hands: number
    net: number
    bb_per_100: number | null
    won_hands: number
    win_rate: number | null
    vpip: Ratio
    pfr: Ratio
    three_bet: Ratio
    fold_to_three_bet: Ratio
    cbet: Ratio
    saw_flop: Ratio
    wtsd: Ratio
    wsd: Ratio
    wwsf: Ratio
    af: number | null
    showdown_net: number
    non_showdown_net: number
  }
  positions: { position: string; hands: number; net: number; bb_per_100: number | null; vpip: number | null }[]
  /** 打过的起手牌格子,如 "AKs" / "AKo" / "AA"。没打过的不在里面。 */
  combos: { combo: string; hands: number; net: number; won: number; vpip: number }[]
  /** 累计盈亏曲线:总 / 摊牌 / 非摊牌。 */
  graph: { n: number; net: number; sd: number; nsd: number }[]
  /** 最近的手,新→旧(最多 300)。 */
  recent: {
    id: number
    end_time: string
    hole_cards: string[] | null
    board: string[]
    net: number
    pot: number
    combo: string | null
    position: string | null
    showed: boolean
  }[]
}

/** 默认看自己;`user` 只在后端开了本地测试开关时有效,否则 403。 */
export async function fetchMyStats(q: { since?: number; user?: string } = {}): Promise<PlayerStats> {
  return postSealed<PlayerStats>('/stats/me', defined({ since: q.since, user: q.user }))
}

/**
 * 手牌历史,新→旧。走信封,需已登录。
 *
 * 分页用游标不用 offset:传 `before=上一页最后一条的 id` 取下一页。返回条数少于 limit 就是到底了。
 */
export async function fetchHands(q: HandsQuery = {}): Promise<HandRecord[]> {
  const params = defined({ user: q.user, room: q.room, before: q.before, limit: q.limit })
  return (await postSealed<{ hands: HandRecord[] }>('/hands', params)).hands
}

/**
 * 从 dedupe_key 取房名。
 *
 * 后端的 HandRecordView **不带独立的 room 字段**(房名只在 dedupe_key 里),所以要展示房名得在这儿还原。
 * 按最后一个冒号切:房名是用户可起的动态名,自己可能含冒号,从左边切会截断。
 */
export function handRoom(record: HandRecord): string {
  const i = record.dedupe_key.lastIndexOf(':')
  return i < 0 ? record.dedupe_key : record.dedupe_key.slice(0, i)
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

/**
 * 改密码。要验旧密码是第二因子:光有会话 token 改不动密码,盗到 token 的人锁不死真用户。
 *
 * 失败按 RestError.status 分:401 信封没过(用新 seq 重试一次再判要不要重登)· 403 旧密码错或该账号未启用密码
 * · 400 缺参/新密码为空 · 500 服务端。
 *
 * 改成功会**吊销该账号在别处的会话**(0097),当前这个留着;那些设备的 ws 会在下一帧被 4401 关掉。
 */
export function changePassword(oldPassword: string, newPassword: string): Promise<{ status: string }> {
  return postSealed<{ status: string }>('/user/password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}

/**
 * 改昵称。**只有在大厅(不在任何房间)时才允许**——昵称是服务器房间状态的键,在房中改会让键错乱。
 *
 * 失败按 RestError.status 分:401 信封没过 · 403 人还在房里 · 409 昵称被占(或并发改名输了)
 * · 400 空/首尾带空白/超 50 字/与现名相同 · 500 服务端。
 * 成功后返回的 nickname 是权威值,要拿它去更新本地的「我是谁」。
 */
export function changeNickname(newNickname: string): Promise<{ status: string; nickname: string }> {
  return postSealed<{ status: string; nickname: string }>('/user/nickname', { new_nickname: newNickname })
}

/**
 * 登出:让服务器吊销这个会话。
 *
 * 不调它的话「退出」只是清本地——服务器上那把 session_token 一直有效到 SESSION_TTL 自然到期,
 * 谁拿到它都还能照常收发(0097 / BUG-8)。只吊销当前这一个会话,别的设备不受影响。
 */
export function logout(): Promise<{ status: string }> {
  return postSealed<{ status: string }>('/user/logout', {})
}
