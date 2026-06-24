from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    INTERNAL = "INTERNAL"  # 未预期 bug;reduce 抛异常被 GameLoop 归一
    NO_SUCH_ROOM = "NO_SUCH_ROOM"  # JoinRoom 目标房不存在
    ROOM_FULL = "ROOM_FULL"  # JoinRoom 满座
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"  # 单房间约束:已在别房
    NOT_IN_ROOM = "NOT_IN_ROOM"  # 命令要求先在房间内
    SEAT_TAKEN = "SEAT_TAKEN"  # SitDown/BuyIn 到已占用座位
    NOT_YOUR_SEAT = "NOT_YOUR_SEAT"  # 操作了不属于自己的座位
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"  # 不在 USER_STATUS_TRANSITIONS 表
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"  # 买入超过全局积分余额
    INVALID_BUY_IN = "INVALID_BUY_IN"  # 买入额非法(≤0;上下限随 gameconfig 收编后补,见 config.md)
    HAND_IN_PROGRESS = "HAND_IN_PROGRESS"  # 手牌进行中不可执行该操作
    NO_HAND = "NO_HAND"  # 该操作需要进行中的手牌
    NOT_YOUR_TURN = "NOT_YOUR_TURN"  # PlayerAction 非当前行动者发起
    ILLEGAL_ACTION = "ILLEGAL_ACTION"  # 动作违反下注规则(rules.md ②)
    NOT_ENOUGH_PLAYERS = "NOT_ENOUGH_PLAYERS"  # StartHand 时在局 ready 玩家 < 2
    NOT_READY = "NOT_READY"  # 该操作要求发起人 READY_TO_PLAY(如 StartHand 发起人)
    NO_VOTE_IN_PROGRESS = "NO_VOTE_IN_PROGRESS"  # VoteFreeEntry 时无进行中投票
    NOT_A_VOTER = "NOT_A_VOTER"  # 非合格投票人却投票
    CANNOT_OPEN_VOTE = "CANNOT_OPEN_VOTE"  # OpenFreeEntryVote 时无 new_here 候选或无合格投票人(rules.md ①)
    INVALID_MESSAGE = "INVALID_MESSAGE"  # 帧非法 JSON / 未知 type / 字段不合法(含房聊空文本);Receiver 解析/防护层直接回发(见 error.md)
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"  # 房聊文本超 ROOM_CHAT_MAX_TEXT_LEN;Receiver 文本防护拒(messaging.md)
    RATE_LIMITED = "RATE_LIMITED"  # 发送过频被令牌桶限速;Receiver 防护拒(messaging.md 契约 4)


@dataclass(frozen=True, slots=True)
class Err:
    code: ErrorCode  # 机器可读码;前端据它映射 UI/文案
    detail: str = ""  # 给人看的上下文(谁、哪个座位、什么状态)
