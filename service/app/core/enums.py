from enum import StrEnum


class RoomStatus(StrEnum):
    PENDING_START = "pending_start"  # 无手牌进行,等待 StartHand
    HAND_STARTED = "hand_started"  # 一手牌进行中


class HandStatus(StrEnum):
    PRE_FLOP = "pre_flop"  # 已发底牌,公共牌前
    FLOP = "flop"  # 前 3 张公共牌
    TURN = "turn"  # 第 4 张公共牌
    RIVER = "river"  # 第 5 张公共牌
    SHOWDOWN = "showdown"  # 摊牌定胜负
    ENDING = "ending"  # 结算并清理本手

    @property
    def next_status(self) -> "HandStatus | None":
        # 下注轮关闭时推进的下一街;RIVER → SHOWDOWN,SHOWDOWN 后无后继
        chain = {
            HandStatus.PRE_FLOP: HandStatus.FLOP,
            HandStatus.FLOP: HandStatus.TURN,
            HandStatus.TURN: HandStatus.RIVER,
            HandStatus.RIVER: HandStatus.SHOWDOWN,
        }
        return chain.get(self)


class PlayerActionType(StrEnum):
    FOLD = "fold"  # 弃牌
    BET = "bet"  # 下注/跟注/加注合并,携带本街目标总额
    CHECK = "check"  # 过牌,仅当无需跟注


class PlayerStatus(StrEnum):
    ACTIVE = "active"  # 本手仍可行动
    FOLDED = "folded"  # 已弃牌
    ALLIN = "allin"  # 全下,不能再行动


class UserStatus(StrEnum):
    WATCHING = "watching"  # 在房未就座(观战)
    OFFLINE = "offline"  # 断线;座位/状态保留,待重连或清理
    SITTING_IN = "sitting_in"  # 已就座,未标记下一手准备
    READY_TO_PLAY = "ready_to_play"  # 已就座且准备好;下一手发牌
    SITTING_OUT = "sitting_out"  # 已就座但跳过手牌
    PLAYING = "playing"  # 已就座且在进行中的手牌里

    def can_change_to(self, new_status: "UserStatus") -> bool:
        # 任意转移(系统或玩家发起);每次状态变更都查这张表
        return (self, new_status) in USER_STATUS_TRANSITIONS

    def userself_can_change_to(self, new_status: "UserStatus") -> bool:
        # 玩家经 SetUserStatus 可主动发起的子集(不含连接/开局这类系统转移)
        return (self, new_status) in USER_STATUS_SELF_TRANSITIONS


# 所有合法 UserStatus 转移(系统 + 玩家)。任何转移前必须查这张表。
USER_STATUS_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    # 断线 / 重连
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
    # 入座
    (UserStatus.WATCHING, UserStatus.SITTING_IN),
    # 起身
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    # 留桌内调整
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    # 牌局流转(开局/结束时系统驱动)
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.PLAYING, UserStatus.SITTING_IN),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
}

# 玩家经 SetUserStatus 可主动发起的子集;不含连接/开局这类系统转移。
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
