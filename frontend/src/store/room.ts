// 房间状态:StateSnapshot 是整份真相,其余事件是它的增量(见 docs/state.md)。
//
// 这里**不复算任何规则**——谁能行动、底池怎么分、谁赢,全部以服务器发来的字段为准。
// 前端只做展示层面的推导(比如把 acting_position 换算成高亮哪个座位)。
//
// 用 useSyncExternalStore 的订阅模型,不引入状态库:状态更新只有一个入口(服务器消息),
// 组件只读不写,没必要上更重的方案。

import type {
  Card,
  ChatMessage,
  ErrorCode,
  HandStatus,
  NickAmount,
  PlayerView,
  RoomStatus,
  SeatView,
  ServerMessage,
  ShowdownReveal,
} from '@/types/wire.gen'
import type { ConnectionState } from '@/transport/ws'

export interface RoomState {
  /** 已进入的房间;null 表示还没 join_room 成功。 */
  room: string | null
  /** 自己的昵称,由 /user/me 取得;用来判断哪个座位是我的。 */
  me: string | null

  maxSeats: number
  buttonPosition: number
  smallBlind: number
  bigBlind: number
  buyIn: number
  roomStatus: RoomStatus

  seats: SeatView[]
  watchers: string[]

  handStatus: HandStatus | null
  board: Card[]
  pot: number
  actingPosition: number | null
  players: PlayerView[]
  /** 本街当前需要跟到的额度,由 PlayerActed 带来;新一手/新街道归零。 */
  lastBet: number
  /** 只有自己的底牌;别人的底牌只在摊牌时经 HandShowDown 出现。 */
  yourHoleCards: [Card, Card] | null

  reveals: ShowdownReveal[]
  lastResult: { winnings: NickAmount[]; refunds: NickAmount[] } | null

  chat: ChatMessage[]
  freeEntryVote: { candidates: string[]; voters: string[]; approvals: string[] } | null

  connection: ConnectionState
  /** 最近一次服务器拒绝;展示后由 UI 清掉。 */
  lastError: { code: ErrorCode; detail: string | null } | null
}

const EMPTY: RoomState = {
  room: null,
  me: null,
  maxSeats: 9,
  buttonPosition: 0,
  smallBlind: 0,
  bigBlind: 0,
  buyIn: 0,
  roomStatus: 'pending_start',
  seats: [],
  watchers: [],
  handStatus: null,
  board: [],
  pot: 0,
  actingPosition: null,
  players: [],
  lastBet: 0,
  yourHoleCards: null,
  reveals: [],
  lastResult: null,
  chat: [],
  freeEntryVote: null,
  connection: 'idle',
  lastError: null,
}

let state: RoomState = EMPTY
const listeners = new Set<() => void>()

function set(patch: Partial<RoomState>): void {
  state = { ...state, ...patch }
  for (const l of listeners) l()
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getRoomState(): RoomState {
  return state
}

export function resetRoom(): void {
  state = EMPTY
  for (const l of listeners) l()
}

export function setMe(nickname: string): void {
  set({ me: nickname })
}

export function setConnection(connection: ConnectionState): void {
  set({ connection })
}

export function clearError(): void {
  if (state.lastError) set({ lastError: null })
}

/** 我的座位;不在座返回 null。 */
export function mySeat(s: RoomState = state): SeatView | null {
  if (!s.me) return null
  return s.seats.find((seat) => seat.nickname === s.me) ?? null
}

/** 我在这一手里的 Player;没在这手里返回 null。 */
export function myPlayer(s: RoomState = state): PlayerView | null {
  if (!s.me) return null
  return s.players.find((p) => p.nickname === s.me) ?? null
}

/** 是否轮到我行动。判据只看服务器给的 acting_position,不自己推。 */
export function isMyTurn(s: RoomState = state): boolean {
  const me = myPlayer(s)
  return me !== null && s.actingPosition !== null && me.seat_position === s.actingPosition
}

/**
 * 把一条服务器消息并进状态。
 *
 * 收到 StateSnapshot 一律**整份替换**,不与本地合并——失去同步时靠重连拿新快照修复,
 * 不靠本地打补丁(见 docs/architecture.md 不变量 2)。
 */
export function applyServerMessage(msg: ServerMessage): void {
  switch (msg.type) {
    case 'state_snapshot':
      set({
        room: msg.room,
        maxSeats: msg.max_seats,
        buttonPosition: msg.button_position,
        smallBlind: msg.small_blind,
        bigBlind: msg.big_blind,
        buyIn: msg.buy_in,
        roomStatus: msg.room_status,
        seats: msg.seats,
        watchers: msg.watchers,
        handStatus: msg.hand_status,
        board: msg.board,
        pot: msg.pot,
        actingPosition: msg.acting_position,
        players: msg.players,
        yourHoleCards: msg.your_hole_cards,
        // 快照不带 last_bet:本街要跟多少可由各家 bet_amount 的最大值还原。
        lastBet: msg.players.reduce((max, p) => Math.max(max, p.bet_amount), 0),
        reveals: [],
        lastResult: null,
      })
      break

    case 'hand_started':
      set({
        roomStatus: 'hand_started',
        handStatus: 'pre_flop',
        buttonPosition: msg.button_position,
        smallBlind: msg.small_blind,
        bigBlind: msg.big_blind,
        players: msg.players,
        actingPosition: msg.acting_position,
        board: [],
        pot: 0,
        lastBet: msg.big_blind,
        yourHoleCards: null,
        reveals: [],
        lastResult: null,
      })
      break

    case 'hole_cards':
      set({ yourHoleCards: msg.cards })
      break

    case 'hand_status_changed':
      // 街道推进:各家本街投入清零、需跟额归零(见 service/docs/rules.md ③)。
      set({
        handStatus: msg.status,
        board: msg.board,
        lastBet: 0,
        players: state.players.map((p) => ({ ...p, bet_amount: 0 })),
      })
      break

    case 'player_acted':
      set({
        players: state.players.map((p) =>
          p.seat_position === msg.seat_position
            ? { ...p, bet_amount: msg.bet_amount, points: msg.points, status: msg.status }
            : p,
        ),
        pot: msg.pot,
        lastBet: msg.last_bet,
        actingPosition: msg.acting_position,
      })
      break

    case 'hand_show_down':
      set({ handStatus: 'showdown', board: msg.board, reveals: msg.reveals, actingPosition: null })
      break

    case 'hand_ended':
      set({
        handStatus: null,
        roomStatus: 'pending_start',
        actingPosition: null,
        lastResult: { winnings: msg.winnings, refunds: msg.refunds },
      })
      break

    case 'user_joined':
      set({ watchers: state.watchers.includes(msg.nickname) ? state.watchers : [...state.watchers, msg.nickname] })
      break

    case 'user_left':
      set({
        watchers: state.watchers.filter((n) => n !== msg.nickname),
        seats: state.seats.filter((s) => s.nickname !== msg.nickname),
      })
      break

    case 'user_status_changed':
      set({
        seats: state.seats.map((s) => (s.nickname === msg.nickname ? { ...s, status: msg.status } : s)),
      })
      break

    case 'player_bought_in':
      set({
        seats: state.seats.map((s) =>
          s.seat_position === msg.seat_position ? { ...s, points: msg.seat_points } : s,
        ),
      })
      break

    case 'room_config_changed':
      set({ smallBlind: msg.small_blind, bigBlind: msg.big_blind, buyIn: msg.buy_in })
      break

    case 'chat_message':
      set({ chat: [...state.chat, msg].slice(-100) })
      break

    case 'room_chat_history':
      set({ chat: msg.messages })
      break

    case 'free_entry_vote_updated':
      set({ freeEntryVote: { candidates: msg.candidates, voters: msg.voters, approvals: msg.approvals } })
      break

    case 'free_entry_vote_closed':
      set({ freeEntryVote: null })
      break

    case 'error':
      set({ lastError: { code: msg.code, detail: msg.detail ?? null } })
      break

    default:
      // 私信等与牌桌无关的消息在别处处理;这里静默忽略,不当成错误。
      break
  }
}
