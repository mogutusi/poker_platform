// 界面操作 → ClientMessage。每个函数只负责发一条命令就结束:
// 界面等服务器回事件再变,不抢先改本地状态(见 docs/architecture.md 不变量 1)。

import { fetchProfile } from '@/transport/rest'
import { connect, disconnect, send, type ConnectionState } from '@/transport/ws'
import { applyServerMessage, getRoomState, resetRoom, setConnection, setMe } from './room'

/**
 * 连上 ws 并进入房间。
 *
 * 顺序:先取自己的昵称(要靠它判断哪个座位是我的),再连 ws,连上后发 join_room。
 * 房间不存在时后端会动态建房(见 service/docs/core.md 房间生命周期)。
 */
export async function enterRoom(
  room: string,
  onAuthLost: () => void,
): Promise<void> {
  const profile = await fetchProfile()
  setMe(profile.nickname)

  connect({
    onMessage: applyServerMessage,
    onStateChange: (s: ConnectionState) => {
      setConnection(s)
      // 每次连上(含重连)都要 join_room:重连后服务器会私发新的 StateSnapshot,
      // 本地状态整份被替换,不需要自己补。
      if (s === 'open') send({ type: 'join_room', room })
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
