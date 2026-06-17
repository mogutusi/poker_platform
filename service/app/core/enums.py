"""core 状态机枚举(四套)+ UserStatus 合法转移表。

迁移自旧 app/pokertable/enums.py,按 core.md 收敛:
- RoomStatus 去掉冗余的 HAND_ENDED(手结束直接回 PENDING_START)。
- HandStatus 去掉 READY_TO_START(开局即 PRE_FLOP);保留 next_status 推进链。
纯数据 + 纯函数,不 import 任何 IO/框架符号(硬规则 1)。
"""

from enum import StrEnum


class RoomStatus(StrEnum):
    PENDING_START = "pending_start"
    HAND_STARTED = "hand_started"


class HandStatus(StrEnum):
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    ENDING = "ending"

    @property
    def next_status(self) -> "HandStatus | None":
        """下注轮关闭时推进到的下一街;RIVER → SHOWDOWN,SHOWDOWN/ENDING 无后继。"""
        chain = {
            HandStatus.PRE_FLOP: HandStatus.FLOP,
            HandStatus.FLOP: HandStatus.TURN,
            HandStatus.TURN: HandStatus.RIVER,
            HandStatus.RIVER: HandStatus.SHOWDOWN,
        }
        return chain.get(self)


class PlayerActionType(StrEnum):
    FOLD = "fold"
    BET = "bet"
    CHECK = "check"


class PlayerStatus(StrEnum):
    """一手牌内某座位的牌局状态(与 UserStatus 正交)。"""

    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"


class UserStatus(StrEnum):
    """一个人在房间里的身份(观战/就座/准备/在玩/离线)。"""

    WATCHING = "watching"
    OFFLINE = "offline"
    SITTING_IN = "sitting_in"
    READY_TO_PLAY = "ready_to_play"
    SITTING_OUT = "sitting_out"
    PLAYING = "playing"

    def can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_TRANSITIONS

    def userself_can_change_to(self, new_status: "UserStatus") -> bool:
        """玩家主动(SetUserStatus)允许的子集;系统驱动的转移(连接/开局)不受此限。"""
        return (self, new_status) in USER_STATUS_SELF_TRANSITIONS


# 所有合法 UserStatus 转移(系统 + 玩家)。任何转移前必须查这张表(core.md)。
USER_STATUS_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    # disconnect / reconnect
    (UserStatus.WATCHING, UserStatus.OFFLINE),
    (UserStatus.READY_TO_PLAY, UserStatus.OFFLINE),
    (UserStatus.PLAYING, UserStatus.OFFLINE),
    (UserStatus.SITTING_IN, UserStatus.OFFLINE),
    (UserStatus.SITTING_OUT, UserStatus.OFFLINE),
    (UserStatus.OFFLINE, UserStatus.WATCHING),
    (UserStatus.OFFLINE, UserStatus.READY_TO_PLAY),
    (UserStatus.OFFLINE, UserStatus.PLAYING),
    (UserStatus.OFFLINE, UserStatus.SITTING_IN),
    (UserStatus.OFFLINE, UserStatus.SITTING_OUT),
    # sit down
    (UserStatus.WATCHING, UserStatus.SITTING_IN),
    # stand up
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    # stay in the table
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    # game flow (system-driven on hand start/end)
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.PLAYING, UserStatus.SITTING_IN),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
}

# 玩家主动可发起的子集(SetUserStatus 校验用);不含连接/开局这类系统转移。
USER_STATUS_SELF_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
}
