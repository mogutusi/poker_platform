// 进房消息的处置决策,抽成纯函数好测(actions.ts 负责真的发命令)。
//
// 要解决的是「上一次会话的残留」:上次在座时断线的用户,后端按不变量 9 保留了他的座位,
// 所以这次新连接走的是**重连**路径——服务器先私发**旧房间**的快照,随后我们的 join_room
// 会被 ALREADY_IN_ROOM 拒。两种迹象都说明还挂在别处,要先退掉再进。
//
// 但 ALREADY_IN_ROOM 有**两种**来路,0087 之前混作一谈:上面那种是「挂在别的房间」,
// 而断线重连回**同一个房间**时它是**预料之中的回答**——服务器已经把我放回原房了,我们
// 每次 open 都照发的那条 join_room 自然被拒。判据是同一条连接上刚收到的快照说我在哪
// (服务器的事实,不是本地推的):快照说我已在目标房 ⇒ 这条错误无害;否则才是残留。

import type { ServerMessage } from '@/types/wire.gen'

export type JoinDecision =
  /** 正常消息,并进房间状态 */
  | { kind: 'apply' }
  /** 还挂在别的房间:先 leave_room 再 join_room */
  | { kind: 'recover' }
  /** 丢弃(旧房间的快照、已经尝试过恢复,或重连时那条预料之中的 ALREADY_IN_ROOM) */
  | { kind: 'ignore' }

export function decideJoinMessage(
  msg: ServerMessage,
  targetRoom: string,
  alreadyRecovered: boolean,
  /** 本条连接上已收下的快照说我在哪个房间;null = 这条连接还没收到过快照 */
  snapshotRoom: string | null = null,
): JoinDecision {
  const staleSnapshot = msg.type === 'state_snapshot' && msg.room !== targetRoom
  const alreadyInRoom = msg.type === 'error' && msg.code === 'ALREADY_IN_ROOM'

  // 重连回同一个房间:服务器先私发本房快照,再拒掉我们那条多余的 join_room。咽掉它。
  // 顺序是有保证的——Receiver 在进收帧循环**之前**就投了 Connect,而单连接严格保序
  // (见 service/docs/architecture.md),所以快照必定先于这条错误到达。
  if (alreadyInRoom && snapshotRoom === targetRoom) return { kind: 'ignore' }

  if (staleSnapshot || alreadyInRoom) {
    // 恢复只做一次。做不成就把错误交给界面显示,不要反复重试转圈。
    return alreadyRecovered ? { kind: 'ignore' } : { kind: 'recover' }
  }
  return { kind: 'apply' }
}
