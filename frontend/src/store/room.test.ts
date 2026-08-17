// 房间状态归并的单测。重点是那些「一旦搞错就会和服务器分叉」的规则:
// 快照整份替换、街道推进要清本街投入、别人的底牌只在摊牌出现。

import { beforeEach, describe, expect, it } from 'vitest'
import type { StateSnapshot } from '@/types/wire.gen'
import { actingPlayer, applyServerMessage, getRoomState, isMyTurn, myPlayer, mySeat, resetRoom, setMe } from './room'
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
    acting_position: 1,
    players: [
      { seat_position: 3, nickname: 'alice', points: 490, bet_amount: 10, status: 'active' },
      { seat_position: 5, nickname: 'bob', points: 680, bet_amount: 20, status: 'active' },
    ],
    your_hole_cards: [
      { rank: 'A', suit: 'h' },
      { rank: 'K', suit: 's' },
    ],
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
  it('清掉各家本街投入与需跟额,不动累计底池', () => {
    applyServerMessage(snapshot())
    applyServerMessage({
      type: 'hand_status_changed',
      status: 'flop',
      board: [
        { rank: '2', suit: 'c' },
        { rank: '7', suit: 'd' },
        { rank: 'T', suit: 'h' },
      ],
    })
    const s = getRoomState()
    expect(s.handStatus).toBe('flop')
    expect(s.board).toHaveLength(3)
    expect(s.lastBet).toBe(0)
    expect(s.players.every((p) => p.bet_amount === 0)).toBe(true)
    expect(s.pot).toBe(30) // 底池是累计的,街道推进不清
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
})
