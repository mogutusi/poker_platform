// 界面操作 → ClientMessage。每个函数只负责发一条命令就结束:
// 界面等服务器回事件再变,不抢先改本地状态(见 docs/architecture.md 不变量 1)。

import { fetchProfile } from '@/transport/rest'
import { decideJoinMessage } from './joinFlow'
import { connect, disconnect, send, type ConnectionState } from '@/transport/ws'
import { applyServerMessage, getRoomState, resetRoom, setConnection, setMe } from './room'

/**
 * 连上 ws 并进入房间。
 *
 * 顺序:先取自己的昵称(要靠它判断哪个座位是我的),再连 ws,连上后发 join_room。
 * 房间不存在时后端会动态建房(见 service/docs/core.md 房间生命周期)。
 */
/**
 * 连上 ws 并进入房间。返回一个取消函数。
 *
 * 必须可取消:本函数要先 await 一次 REST 拿昵称,而组件可能在这期间就卸载了
 * (React 严格模式下开发期 effect 会跑两遍,必然撞上)。不取消的话,await 回来后
 * 仍会去连一条没人要的连接。
 */
export async function enterRoom(
  room: string,
  onAuthLost: () => void,
  isCancelled: () => boolean = () => false,
): Promise<void> {
  const profile = await fetchProfile()
  if (isCancelled()) return
  setMe(profile.nickname)

  // 「先退再进」只做一次。做不成就把错误交给界面显示,不要反复重试转圈。
  let recovered = false

  /**
   * 处理「上一次会话的残留」。
   *
   * 上次在座时断线的用户,后端会保留他的座位(不变量 9:一个用户同时只在一个房间)。
   * 于是这次新连接触发的是**重连**路径:服务器先私发**旧房间**的快照,随后我们的
   * join_room 会被 ALREADY_IN_ROOM 拒。两种迹象都说明还挂在别处,先退掉再进。
   */
  function recoverFromStaleRoom(): void {
    if (recovered) return
    recovered = true
    send({ type: 'leave_room' })
    send({ type: 'join_room', room })
  }

  connect({
    onMessage: (msg) => {
      const decision = decideJoinMessage(msg, room, recovered)
      if (decision.kind === 'recover') {
        recoverFromStaleRoom()
        return
      }
      // 旧房间的快照不并进本地状态——那是要离开的房间。
      if (decision.kind === 'ignore') return
      applyServerMessage(msg)
    },
    onStateChange: (s: ConnectionState) => {
      setConnection(s)
      // 每次连上(含重连)都要 join_room:重连后服务器会私发新的 StateSnapshot,
      // 本地状态整份被替换,不需要自己补。
      if (s === 'open') {
        recovered = false // 新连接重新给一次恢复机会
        send({ type: 'join_room', room })
      }
    },
    onAuthLost: () => {
      resetRoom()
      onAuthLost()
    },
  })
}

export function leaveRoom(): void {
  send({ type: 'leave_room' })
}

export function closeRoom(): void {
  disconnect()
  resetRoom()
}

/** 入座。wait_for_big_blind=true 是「等大盲免费」,false 是默认的「付盲即玩」。 */
export function sitDown(seat: number, waitForBigBlind: boolean): void {
  send({ type: 'sit_down', seat, wait_for_big_blind: waitForBigBlind })
}

export function buyIn(seat: number, amount: number): void {
  send({ type: 'buy_in', seat, amount })
}

export function setReady(ready: boolean, seat: number): void {
  send({ type: 'set_user_status', status: ready ? 'ready_to_play' : 'sitting_in', seat })
}

export function startHand(seat: number): void {
  send({ type: 'start_hand', seat })
}

export function fold(): void {
  send({ type: 'player_action', action: 'fold' })
}

export function check(): void {
  send({ type: 'player_action', action: 'check' })
}

/**
 * 下注 / 跟注 / 加注 / all-in 在协议上都是 bet,区别只在金额。
 * amount 是**本街目标总额**,不是这次要加多少(见 service/docs/rules.md ②)。
 */
export function bet(amount: number): void {
  send({ type: 'player_action', action: 'bet', bet_amount: amount })
}

export function sendChat(text: string): void {
  send({ type: 'room_chat', text })
}

export function fetchChatHistory(): void {
  const room = getRoomState().room
  if (room) send({ type: 'fetch_room_chat', room })
}
