// 组件读状态的入口。useSyncExternalStore 保证并发渲染下读到的是一致快照。

'use client'

import { useSyncExternalStore } from 'react'
import { getRoomState, subscribe, type RoomState } from './room'

/** 服务端渲染阶段没有房间状态,给一个稳定的空态,避免 hydration 不一致。 */
const SERVER_SNAPSHOT = getRoomState()

export function useRoom(): RoomState {
  return useSyncExternalStore(subscribe, getRoomState, () => SERVER_SNAPSHOT)
}
