// 房间状态归并的单测。重点是那些「一旦搞错就会和服务器分叉」的规则:
// 快照整份替换、街道推进要清本街投入、别人的底牌只在摊牌出现。

import { beforeEach, describe, expect, it } from 'vitest'
import type { StateSnapshot } from '@/types/wire.gen'
import {
  actingPlayer,
  applyServerMessage,
  clearResult,
  getRoomState,
  isMyTurn,
  myPlayer,
  mySeat,
  resetRoom,
  setConnection,
  setMe,
} from './room'
import { decideJoinMessage } from './joinFlow'

function snapshot(over: Partial<StateSnapshot> = {}): StateSnapshot {
  return {
    type: 'state_snapshot',
    room: 'dev',
    max_seats: 9,
    button_position: 0,
    small_blind: 10,
    big_blind: 20,
    buy_in: 1000,
    room_status: 'hand_started',
    // 故意让座位号 ≠ players 下标:alice 坐 3 号位是 players[0],bob 坐 5 号位是 players[1]。
    // acting_position 是 players 的下标,拿它当座位号用会在这里露馅。
    seats: [
      { seat_position: 3, nickname: 'alice', status: 'playing', points: 500, new_here: false },
      { seat_position: 5, nickname: 'bob', status: 'playing', points: 700, new_here: false },
    ],
    watchers: ['carol'],
    hand_status: 'pre_flop',
    board: [],
    pot: 30,
    last_bet: 20,
    min_raise_to: 40,
    acting_position: 1,
    players: [
      { seat_position: 3, nickname: 'alice', points: 490, bet_amount: 10, status: 'active' },
      { seat_position: 5, nickname: 'bob', points: 680, bet_amount: 20, status: 'active' },
    ],
    your_hole_cards: [
      { rank: 'A', suit: 'h' },
      { rank: 'K', suit: 's' },
    ],
    free_entry_vote: null,
    ...over,
  }
}

beforeEach(() => {
  resetRoom()
  setMe('alice')
})

describe('StateSnapshot', () => {
  it('整份替换,不与旧状态合并', () => {
    applyServerMessage(snapshot())
    applyServerMessage(snapshot({ seats: [], players: [], watchers: [], pot: 0, your_hole_cards: null }))
    const s = getRoomState()
    expect(s.seats).toEqual([])
    expect(s.players).toEqual([])
    expect(s.pot).toBe(0)
    expect(s.yourHoleCards).toBeNull()
  })

  it('本街需跟额由各家 bet_amount 的最大值还原(快照不带 last_bet)', () => {
    applyServerMessage(snapshot())
    expect(getRoomState().lastBet).toBe(20)
  })

  it('认得出自己的座位、自己的 Player 和是否轮到自己', () => {
    applyServerMessage(snapshot())
    expect(mySeat()?.nickname).toBe('alice')
    expect(myPlayer()?.seat_position).toBe(3)
    expect(isMyTurn()).toBe(false) // acting_position=1 是 players[1]=bob

    applyServerMessage(snapshot({ acting_position: 0 })) // players[0]=alice
    expect(isMyTurn()).toBe(true)
    expect(actingPlayer()?.nickname).toBe('alice')
  })
})

describe('街道推进', () => {
  it('本街下注态照服务器给的填,不动累计底池', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_status_changed',
      status: 'flop',
      board: [
        { rank: '2', suit: 'c' },
        { rank: '7', suit: 'd' },
        { rank: 'T', suit: 'h' },
      ],
      last_bet: 0,
      min_raise_to: 20,
      players: [
        { seat_position: 3, nickname: 'alice', points: 490, bet_amount: 0, status: 'active' },
        { seat_position: 5, nickname: 'bob', points: 680, bet_amount: 0, status: 'active' },
      ],
    })
    const s = getRoomState()
    expect(s.handStatus).toBe('flop')
    expect(s.board).toHaveLength(3)
    expect(s.lastBet).toBe(0)
    expect(s.players.every((p) => p.bet_amount === 0)).toBe(true)
    expect(s.pot).toBe(30) // 底池是累计的,街道推进不清
  })

  it('开局那条 PRE_FLOP 不许把盲注清掉', () => {
    // 服务端在 HandStarted 之后紧跟一条 status=PRE_FLOP 的 hand_status_changed。此前前端把它
    // 当「换街」处理、把 lastBet 和各家 bet_amount 一律清零,于是整轮 preflop 的 Call 都发成
    // bet(0),被 ILLEGAL_ACTION 拒(0087 在浏览器里抓到)。现在一律照服务器给的填。
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_status_changed',
      status: 'pre_flop',
      board: [],
      last_bet: 20,
      min_raise_to: 40,
      players: [
        { seat_position: 3, nickname: 'alice', points: 490, bet_amount: 10, status: 'active' },
        { seat_position: 5, nickname: 'bob', points: 680, bet_amount: 20, status: 'active' },
      ],
    })
    const s = getRoomState()
    expect(s.lastBet).toBe(20)
    expect(s.players.map((p) => p.bet_amount)).toEqual([10, 20])
  })
})

describe('加注下限与免盲投票投影(0088)', () => {
  it('下限一律用服务器给的数,不自己套公式', () => {
    applyServerMessage(snapshot({ last_bet: 20, min_raise_to: 60 }))
    // 60 不等于 lastBet + bigBlind(40),也不等于 callAmount*2 —— 正是前端旧式子会算错的那种局面
    expect(getRoomState().minRaiseTo).toBe(60)
    applyServerMessage({
      type: 'player_acted',
      seat_position: 5,
      nickname: 'bob',
      action: 'bet',
      bet_amount: 60,
      points: 640,
      status: 'active',
      last_bet: 60,
      min_raise_to: 100,
      pot: 70,
      acting_position: 0,
    })
    expect(getRoomState().minRaiseTo).toBe(100)
  })

  it('快照带着进行中的免盲投票,重连之后面板还在(BUG-9)', () => {
    applyServerMessage(
      snapshot({
        free_entry_vote: { candidates: ['dave'], voters: ['alice', 'bob'], approvals: ['alice'] },
      }),
    )
    expect(getRoomState().freeEntryVote).toEqual({
      candidates: ['dave'],
      voters: ['alice', 'bob'],
      approvals: ['alice'],
    })
  })

  it('投票终结要把结果留下来,不是把面板一关了事(0089)', () => {
    applyServerMessage({
      type: 'free_entry_vote_updated',
      candidates: ['dave'],
      voters: ['alice'],
      approvals: [],
    })
    applyServerMessage({ type: 'free_entry_vote_closed', passed: true, waived: ['dave'] })
    expect(getRoomState().freeEntryVote).toBeNull()
    expect(getRoomState().lastVoteResult).toEqual({ passed: true, waived: ['dave'] })
  })

  it('没有投票进行时快照把面板清掉,不留上一轮的残影', () => {
    applyServerMessage({
      type: 'free_entry_vote_updated',
      candidates: ['dave'],
      voters: ['alice'],
      approvals: [],
    })
    expect(getRoomState().freeEntryVote).not.toBeNull()
    applyServerMessage(snapshot())
    expect(getRoomState().freeEntryVote).toBeNull()
  })
})

describe('PlayerActed', () => {
  it('只更新该座位,其余座位不动', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'player_acted',
      seat_position: 5,
      nickname: 'bob',
      action: 'bet',
      bet_amount: 60,
      points: 640,
      status: 'active',
      last_bet: 60,
      min_raise_to: 100,
      pot: 70,
      acting_position: 0,
    })
    const s = getRoomState()
    expect(s.players.find((p) => p.seat_position === 5)).toMatchObject({ bet_amount: 60, points: 640 })
    expect(s.players.find((p) => p.seat_position === 3)).toMatchObject({ bet_amount: 10, points: 490 })
    expect(s.lastBet).toBe(60)
    expect(s.pot).toBe(70)
    expect(isMyTurn()).toBe(true)
  })
})

describe('隐私', () => {
  it('摊牌前只有自己的底牌', () => {
    applyServerMessage(snapshot())
    expect(getRoomState().yourHoleCards).not.toBeNull()
    expect(getRoomState().reveals).toEqual([])
  })

  it('摊牌是别人底牌唯一的来源', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_show_down',
      board: [],
      reveals: [
        {
          seat_position: 5,
          nickname: 'bob',
          hole_cards: [
            { rank: 'Q', suit: 'c' },
            { rank: 'J', suit: 'c' },
          ],
        },
      ],
    })
    const s = getRoomState()
    expect(s.handStatus).toBe('showdown')
    expect(s.reveals).toHaveLength(1)
    expect(s.actingPosition).toBeNull() // 摊牌后没人该行动
  })

  it('新一手开始必须清掉上一手的亮牌,否则上一手对手的底牌会一直挂在新牌局上', () => {
    // 这条从 0105 起是**隐私红线**,不再只是整洁性:在那之前 reveals 根本没渲染过任何东西,
    // 漏清也看不出来;0105 让它上了屏并且跨过 hand_ended 继续显示(结算展示期),于是
    // 「hand_started 清 reveals」成了唯一挡住「整局新牌里对手底牌朝上」的东西。
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_show_down',
      board: [],
      reveals: [
        {
          seat_position: 5,
          nickname: 'bob',
          hole_cards: [
            { rank: 'Q', suit: 'c' },
            { rank: 'J', suit: 'c' },
          ],
        },
      ],
    })
    applyServerMessage({ type: 'hand_ended', winnings: [], refunds: [] })
    expect(getRoomState().reveals).toHaveLength(1) // 结算展示期:牌还留着

    applyServerMessage({
      type: 'hand_started',
      hand_seq: 4,
      button_position: 0,
      small_blind: 10,
      big_blind: 20,
      players: [],
      acting_position: null,
      pot: 30,
      last_bet: 20,
      min_raise_to: 40,
    })
    expect(getRoomState().reveals).toEqual([]) // 新一手:必须清干净
  })

  it('重连拿到的快照也清亮牌:服务器不保存已结束手牌的摊牌,前端不许自己留一份', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_show_down',
      board: [],
      reveals: [
        {
          seat_position: 5,
          nickname: 'bob',
          hole_cards: [
            { rank: 'Q', suit: 'c' },
            { rank: 'J', suit: 'c' },
          ],
        },
      ],
    })
    applyServerMessage(snapshot())
    expect(getRoomState().reveals).toEqual([])
  })
})

describe('手牌结束', () => {
  it('回到等待开局,清掉行动者', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_ended',
      winnings: [{ nickname: 'alice', amount: 70 }],
      refunds: [],
    })
    const s = getRoomState()
    expect(s.handStatus).toBeNull()
    expect(s.roomStatus).toBe('pending_start')
    expect(s.actingPosition).toBeNull()
    expect(s.lastResult?.winnings[0].amount).toBe(70)
  })
})

describe('错误', () => {
  it('记下服务器回的 code 供界面提示', () => {
    applyServerMessage({ type: 'error', code: 'NOT_YOUR_TURN' })
    expect(getRoomState().lastError?.code).toBe('NOT_YOUR_TURN')
  })
})

describe('进房时的「上次会话残留」', () => {
  // 上次在座断线的用户,后端保留了他的座位。新连接走重连路径,服务器先发旧房间的快照,
  // 随后 join_room 会被 ALREADY_IN_ROOM 拒。两种迹象都要触发「先退再进」。
  it('收到别的房间的快照 → 恢复', () => {
    expect(decideJoinMessage(snapshot({ room: 'old' }), 'new', false)).toEqual({ kind: 'recover' })
  })

  it('收到 ALREADY_IN_ROOM → 恢复', () => {
    expect(decideJoinMessage({ type: 'error', code: 'ALREADY_IN_ROOM' }, 'new', false)).toEqual({
      kind: 'recover',
    })
  })

  it('恢复只做一次,之后丢弃,不反复重试转圈', () => {
    expect(decideJoinMessage(snapshot({ room: 'old' }), 'new', true)).toEqual({ kind: 'ignore' })
    expect(decideJoinMessage({ type: 'error', code: 'ALREADY_IN_ROOM' }, 'new', true)).toEqual({
      kind: 'ignore',
    })
  })

  it('目标房间的快照与其它错误照常处理', () => {
    expect(decideJoinMessage(snapshot({ room: 'new' }), 'new', false)).toEqual({ kind: 'apply' })
    expect(decideJoinMessage({ type: 'error', code: 'NOT_YOUR_TURN' }, 'new', false)).toEqual({
      kind: 'apply',
    })
  })

  // 断线重连回**同一个**房间时,服务器已经把我放回原房、私发了本房快照,随后才拒掉我们那条
  // 每次 open 都发的 join_room。此时的 ALREADY_IN_ROOM 是预料之中的回答,不是「挂在别处」——
  // 当成残留去「先退再进」,等于每次重连都把自己从座位上退下来(0087 在浏览器里实测到)。
  it('已经收到目标房快照后再收 ALREADY_IN_ROOM → 咽掉,不许当成残留', () => {
    expect(decideJoinMessage({ type: 'error', code: 'ALREADY_IN_ROOM' }, 'new', false, 'new')).toEqual({
      kind: 'ignore',
    })
  })

  it('快照说我在别的房 → ALREADY_IN_ROOM 仍然是残留,照旧先退再进', () => {
    expect(decideJoinMessage({ type: 'error', code: 'ALREADY_IN_ROOM' }, 'new', false, 'old')).toEqual({
      kind: 'recover',
    })
  })
})

describe('UserStatusChanged 的三种情形', () => {
  // 后端用同一条事件表达入座 / 在座内变状态 / 起身。只处理其中一种会静默吞掉另外两种——
  // 最初只做了「在已有座位里改状态」,于是观战者点入座后界面毫无反应(0080 由浏览器测试发现)。
  it('观战 → 入座:seats 里没有这个人时要新增一条', () => {
    applyServerMessage(snapshot({ seats: [], players: [], watchers: ['alice'] }))
    applyServerMessage({ type: 'user_status_changed', nickname: 'alice', status: 'sitting_in', seat_position: 2, new_here: true })
    const s = getRoomState()
    expect(s.seats).toHaveLength(1)
    expect(s.seats[0]).toMatchObject({ nickname: 'alice', seat_position: 2, status: 'sitting_in' })
    expect(s.watchers).not.toContain('alice') // 已经不是观战者了
    expect(mySeat()?.seat_position).toBe(2)
  })

  it('在座内变状态:就地改,不新增', () => {
    applyServerMessage(snapshot())
    applyServerMessage({ type: 'user_status_changed', nickname: 'alice', status: 'ready_to_play', seat_position: 3, new_here: false })
    const s = getRoomState()
    expect(s.seats).toHaveLength(2)
    expect(s.seats.find((x) => x.nickname === 'alice')?.status).toBe('ready_to_play')
  })

  it('new_here 照抄服务器,不本地猜(0084)', () => {
    // 0084 之前这里硬写 new_here: true,是前端替服务器裁定规则(破前端不变量 1),
    // 而且打完一手就过期——服务端在 _start_hand 末尾重标 new_here 时以前不发任何事件。
    applyServerMessage(snapshot({ seats: [], players: [], watchers: ['alice'] }))
    applyServerMessage({ type: 'user_status_changed', nickname: 'alice', status: 'sitting_in', seat_position: 2, new_here: false })
    expect(getRoomState().seats[0].new_here).toBe(false) // 猜 true 的实现在这里必红

    // 开局重标:服务器说他又欠一个入局盲了,本地那份要跟着变
    applyServerMessage({ type: 'user_status_changed', nickname: 'alice', status: 'sitting_out', seat_position: 2, new_here: true })
    expect(getRoomState().seats[0].new_here).toBe(true)
  })

  it('起身:seat_position 为 null,要从 seats 移除并回到观战', () => {
    applyServerMessage(snapshot())
    applyServerMessage({ type: 'user_status_changed', nickname: 'alice', status: 'watching', seat_position: null, new_here: null })
    const s = getRoomState()
    expect(s.seats.map((x) => x.nickname)).toEqual(['bob'])
    expect(s.watchers).toContain('alice')
    expect(mySeat()).toBeNull()
  })
})

describe('结算面板的生命周期', () => {
  // 结算结果 store 一直收着,但 0081 之前没接到界面上——打完一手用户看不到赢了多少。
  it('hand_ended 记下赢取与退还,两者分开', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_ended',
      winnings: [{ nickname: 'alice', amount: 120 }],
      refunds: [{ nickname: 'bob', amount: 40 }],
    })
    const s = getRoomState()
    expect(s.lastResult?.winnings).toEqual([{ nickname: 'alice', amount: 120 }])
    // 退还是「没人跟的注还给你」,不是赢来的钱,不能并进 winnings
    expect(s.lastResult?.refunds).toEqual([{ nickname: 'bob', amount: 40 }])
  })

  it('新一手开始要清掉上一手的结算,否则面板会挂在新牌局上', () => {
    applyServerMessage(snapshot())
    applyServerMessage({ type: 'hand_ended', winnings: [{ nickname: 'alice', amount: 10 }], refunds: [] })
    expect(getRoomState().lastResult).not.toBeNull()

    applyServerMessage({
      type: 'hand_started',
      hand_seq: 2,
      button_position: 0,
      small_blind: 10,
      big_blind: 20,
      players: [],
      acting_position: null,
      pot: 0,
      last_bet: 20,
      min_raise_to: 40,
    })
    expect(getRoomState().lastResult).toBeNull()
  })

  it('开局底池照服务器给的算:盲注已经在池子里,不是 0', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_started',
      hand_seq: 3,
      button_position: 0,
      small_blind: 10,
      big_blind: 20,
      players: [],
      acting_position: null,
      pot: 30,
      last_bet: 20,
      min_raise_to: 40,
    })
    expect(getRoomState().pot).toBe(30)
  })

  it('用户手动关掉后不再显示', () => {
    applyServerMessage(snapshot())
    applyServerMessage({ type: 'hand_ended', winnings: [], refunds: [] })
    clearResult()
    expect(getRoomState().lastResult).toBeNull()
  })
})

describe('连接状态', () => {
  it('setConnection 会推给订阅者,界面据此显示横幅', () => {
    setConnection('reconnecting')
    expect(getRoomState().connection).toBe('reconnecting')
    setConnection('open')
    expect(getRoomState().connection).toBe('open')
  })
})
