// 进房消息的处置决策,抽成纯函数好测(actions.ts 负责真的发命令)。
//
// 要解决的是「上一次会话的残留」:上次在座时断线的用户,后端按不变量 9 保留了他的座位,
// 所以这次新连接走的是**重连**路径——服务器先私发**旧房间**的快照,随后我们的 join_room
// 会被 ALREADY_IN_ROOM 拒。两种迹象都说明还挂在别处,要先退掉再进。

import type { ServerMessage } from '@/types/wire.gen'

export type JoinDecision =
  /** 正常消息,并进房间状态 */
  | { kind: 'apply' }
  /** 还挂在别的房间:先 leave_room 再 join_room */
  | { kind: 'recover' }
  /** 丢弃(旧房间的快照,或已经尝试过恢复) */
  | { kind: 'ignore' }

export function decideJoinMessage(
  msg: ServerMessage,
  targetRoom: string,
  alreadyRecovered: boolean,
): JoinDecision {
  const staleSnapshot = msg.type === 'state_snapshot' && msg.room !== targetRoom
  const alreadyInRoom = msg.type === 'error' && msg.code === 'ALREADY_IN_ROOM'

  if (staleSnapshot || alreadyInRoom) {
    // 恢复只做一次。做不成就把错误交给界面显示,不要反复重试转圈。
    return alreadyRecovered ? { kind: 'ignore' } : { kind: 'recover' }
  }
  return { kind: 'apply' }
}
