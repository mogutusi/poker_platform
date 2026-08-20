// 组件读私聊状态的入口,同 useRoom。

'use client'

import { useSyncExternalStore } from 'react'
import { getDmState, subscribe, type DmState } from './dm'

/** 服务端渲染阶段没有私聊状态,给一个稳定的空态,避免 hydration 不一致。 */
const SERVER_SNAPSHOT = getDmState()

export function useDm(): DmState {
  return useSyncExternalStore(subscribe, getDmState, () => SERVER_SNAPSHOT)
}
