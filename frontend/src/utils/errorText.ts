// 错误码 → 中文文案。
//
// 后端只回机器可读的 code,文案归前端(见 service/docs/error.md:「多语言文案不在协议里」)。
// 此前界面把 code 原样显示给用户,`NOT_ENOUGH_PLAYERS` 这种东西对玩家毫无意义。
//
// 写文案的两条原则:
//   - 说清「发生了什么」,能说就再说「该怎么办」;
//   - 不猜后端没说的事。比如 INTERNAL 就老实说服务器出错了,不要编一个像模像样的原因。

import type { ErrorCode } from '@/types/wire.gen'

const TEXT: Record<ErrorCode, string> = {
  INTERNAL: '服务器出错了,请重试',
  NO_SUCH_ROOM: '房间不存在',
  ROOM_FULL: '房间已满',
  ALREADY_IN_ROOM: '你已经在另一个房间里,先离开那边',
  NOT_IN_ROOM: '你不在这个房间里',
  SEAT_TAKEN: '这个座位已经有人了',
  NOT_YOUR_SEAT: '这不是你的座位',
  INVALID_STATUS_TRANSITION: '当前状态不能这么操作',
  INSUFFICIENT_POINTS: '积分不够',
  INVALID_BUY_IN: '买入额超出允许范围',
  INVALID_SMALL_BLIND: '小盲额超出允许范围',
  HAND_IN_PROGRESS: '手牌进行中,等这手打完再改',
  NO_HAND: '现在没有进行中的手牌',
  NOT_YOUR_TURN: '还没轮到你',
  ILLEGAL_ACTION: '这个动作不合规则',
  NOT_ENOUGH_PLAYERS: '人数不够,至少要两个准备好的玩家',
  NOT_READY: '还没准备好',
  NO_VOTE_IN_PROGRESS: '现在没有进行中的投票',
  NOT_A_VOTER: '你不是本次投票的投票人',
  CANNOT_OPEN_VOTE: '现在开不了投票:要么没有等入局的新人,要么没有合格的投票人',
  INVALID_MESSAGE: '消息格式不对',
  MESSAGE_TOO_LONG: '消息太长了',
  RATE_LIMITED: '发得太快了,慢一点',
  CANNOT_DM_SELF: '不能给自己发私信',
}

/** 取中文文案;遇到本表还没收录的新码,退回显示原码而不是显示空白。 */
export function errorText(code: ErrorCode): string {
  return TEXT[code] ?? code
}
