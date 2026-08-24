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
import { applyDmMessage } from './dm'

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
  /** 本街当前需要跟到的额度。服务器给的,前端不推(见 changes/0087)。 */
  lastBet: number
  /** 自愿加注的合法下限(本街目标总额)。规则是 `last_bet + max(last_raise_size, BB)`,
   *  但**公式在服务器**——前端只显示这个数,不自己套式子(见 changes/0088 / BUG-19)。
   *  all-in 不受它限制:筹码不够时可以直接推全部。 */
  minRaiseTo: number
  /** 只有自己的底牌;别人的底牌只在摊牌时经 HandShowDown 出现。 */
  yourHoleCards: [Card, Card] | null

  reveals: ShowdownReveal[]
  lastResult: { winnings: NickAmount[]; refunds: NickAmount[] } | null

  chat: ChatMessage[]
  freeEntryVote: { candidates: string[]; voters: string[]; approvals: string[] } | null
  /** 上一次免盲投票的结果(服务器给的 passed / waived)。展示后由 UI 清掉;新一手也会清。
   *  此前这条事件只被用来关面板,结果直接丢掉——投没投过、谁被免了,界面上一个字都没有。 */
  lastVoteResult: { passed: boolean; waived: string[] } | null

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
  minRaiseTo: 0,
  yourHoleCards: null,
  reveals: [],
  lastResult: null,
  chat: [],
  freeEntryVote: null,
  lastVoteResult: null,
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

/** 关掉结算面板。新一手开始时 hand_started 会自己清,这个是给用户手动关的。 */
export function clearResult(): void {
  if (state.lastResult) set({ lastResult: null })
}

/** 关掉免盲投票结果提示。同上,新一手也会自己清。 */
export function clearVoteResult(): void {
  if (state.lastVoteResult) set({ lastVoteResult: null })
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

/**
 * 当前该行动的玩家。
 *
 * **`acting_position` 是 `players[]` 的下标,不是座位号**(见 service/docs/wire-protocol-guide.md)。
 * 两者只有在「每个人的座位号恰好等于其行动序下标」时才相等,一般情况下不等——
 * players 按行动序排([0]=小盲),座位号是桌上的物理位置。
 */
export function actingPlayer(s: RoomState = state): PlayerView | null {
  if (s.actingPosition === null) return null
  return s.players[s.actingPosition] ?? null
}

/** 是否轮到我行动。判据只看服务器给的 acting_position,不自己推。 */
export function isMyTurn(s: RoomState = state): boolean {
  const acting = actingPlayer(s)
  return acting !== null && acting.nickname === s.me
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
        lastBet: msg.last_bet,
        minRaiseTo: msg.min_raise_to,
        // 进行中的免盲投票也在快照里(0088 / BUG-9):重连和顶替只发快照、不重发投票事件,
        // 不投影的话面板会凭空消失,而全票制下少一个人表态就是永久卡住。
        freeEntryVote: msg.free_entry_vote
          ? {
              candidates: msg.free_entry_vote.candidates,
              voters: msg.free_entry_vote.voters,
              approvals: msg.free_entry_vote.approvals,
            }
          : null,
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
        // 盲注已经下了,开局底池不是 0。这里曾硬写 0,于是界面整条 preflop 都显示「底池 0」,
        // 而同一时刻重连拿到的快照写着 3 —— 0087 在浏览器里正是被这个矛盾抓出来的。
        pot: msg.pot,
        lastBet: msg.last_bet,
        minRaiseTo: msg.min_raise_to,
        yourHoleCards: null,
        reveals: [],
        lastResult: null,
        lastVoteResult: null, // 新一手开始,上一次投票的结果不再相关
      })
      break

    case 'hole_cards':
      set({ yourHoleCards: msg.cards })
      break

    case 'hand_status_changed':
      // 本街的下注态一律照服务器给的填。这里曾经自己推「换街了所以全清零」,而开局那条
      // status=PRE_FLOP 紧跟在 HandStarted 之后、盲注**已经下了** —— 一清零,整轮 preflop 的
      // lastBet 就是 0,Call 按钮发出去的 bet(0) 全被 ILLEGAL_ACTION 拒(0087 在浏览器里抓到)。
      // 推规则本来就是前端不该做的事(见 docs/architecture.md 不变量 1)。
      set({
        handStatus: msg.status,
        board: msg.board,
        lastBet: msg.last_bet,
        minRaiseTo: msg.min_raise_to,
        players: msg.players,
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
        minRaiseTo: msg.min_raise_to,
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

    case 'user_status_changed': {
      // 这条事件兼三种情形,不能只在已有座位里找人改状态:
      //   观战 → 入座:seats 里还没有他,要**新增**一条(这一条最初漏了,观战者点入座后界面毫无反应)
      //   在座内变状态(ready / sit-out / 断线):就地改
      //   起身 → 观战:seat_position 为 null,要把他从 seats 里**移除**
      if (msg.seat_position === null) {
        set({
          seats: state.seats.filter((s) => s.nickname !== msg.nickname),
          watchers: state.watchers.includes(msg.nickname) ? state.watchers : [...state.watchers, msg.nickname],
        })
        break
      }
      // new_here 一律照抄服务器(0084 起这条消息带它)。此前这里硬写 true,是前端在替服务器裁定规则
      // ——虽然当时恰好猜对(_sit_down 建的座位确实 new_here=true),但它是猜,而且打完一手就过期:
      // 服务端在 _start_hand 末尾重标 new_here 时以前不发任何事件。现在它会发,照收即可。
      const newHere = msg.new_here ?? false
      const known = state.seats.find((s) => s.nickname === msg.nickname)
      const seats = known
        ? state.seats.map((s) =>
            s.nickname === msg.nickname
              ? { ...s, status: msg.status, seat_position: msg.seat_position!, new_here: newHere }
              : s,
          )
        : [
            ...state.seats,
            // 新入座的人:筹码要等 player_bought_in 或下一次快照才知道,先记 0。
            { seat_position: msg.seat_position, nickname: msg.nickname, status: msg.status, points: 0, new_here: newHere },
          ]
      set({ seats, watchers: state.watchers.filter((n) => n !== msg.nickname) })
      break
    }

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
      // 关面板,同时把结果留下来给界面显示。服务器已经说了 passed / waived,丢掉不用的话
      // 用户只会看到面板凭空消失,不知道自己那一票有没有起作用、谁被免了(0089)。
      set({ freeEntryVote: null, lastVoteResult: { passed: msg.passed, waived: msg.waived } })
      break

    case 'error':
      set({ lastError: { code: msg.code, detail: msg.detail ?? null } })
      break

    case 'dm_delivered':
    case 'dm_read':
    case 'dm_undelivered':
      // 私聊与牌桌无关(跨房间存在、有未读),状态在 dm.ts;这里只转发,不碰它的内部。
      applyDmMessage(msg)
      break

    default:
      // 还没接的消息静默忽略,不当成错误。
      break
  }
}
