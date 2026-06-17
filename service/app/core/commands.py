"""Command 全集(开放集合,见 core.md)。

模型 2:游戏命令**不带 room**——目标房 = world.users[origin].room,由 checkout/reduce
解析(唯一例外 JoinRoom 自带 room)。`origin` 是发起命令的 nick(连接绑 nick);
系统命令(Connect/Disconnect/Timeout/Cleanup)`origin=None`,失败只落日志。

客户端命令与 wire ClientMessage 1:1;系统命令没有报文。
"""

from dataclasses import dataclass
from datetime import datetime

from app.core.cards import Card
from app.core.enums import PlayerActionType, UserStatus


@dataclass(frozen=True, slots=True)
class Command:
    """所有命令的基类;`origin` = 发起人 nick(系统命令为 None)。"""

    origin: str | None


# ---- wire 命令(origin = nick)----


@dataclass(frozen=True, slots=True)
class JoinRoom(Command):
    """大厅→房间。room 在命令里(唯一带 room 的命令);uid/loaded 是 shell 从 DB
    读出的账号主键与积分,由 reduce 决定是否装入 world.users(见 storage.md / lobby.md)。"""

    room: str
    uid: int
    loaded: int  # 该账号当前全局积分


@dataclass(frozen=True, slots=True)
class LeaveRoom(Command):
    pass


@dataclass(frozen=True, slots=True)
class SitDown(Command):
    seat: int


@dataclass(frozen=True, slots=True)
class BuyIn(Command):
    seat: int
    amount: int


@dataclass(frozen=True, slots=True)
class SetUserStatus(Command):
    status: UserStatus
    seat: int | None = None


@dataclass(frozen=True, slots=True)
class SetSmallBlind(Command):
    amount: int


@dataclass(frozen=True, slots=True)
class SetBuyIn(Command):
    amount: int


@dataclass(frozen=True, slots=True)
class StartHand(Command):
    """started_at(墙钟)由 shell 盖好带入;deck 可选(测试/重放注入确定牌堆,
    生产不传 → core/deck.py 用 SystemRandom 洗),见 core.md / testing.md。"""

    seat: int
    started_at: datetime
    deck: list[Card] | None = None


@dataclass(frozen=True, slots=True)
class PlayerAction(Command):
    action: PlayerActionType
    bet_amount: int | None = None  # 本街目标总额(BET 时必填)


@dataclass(frozen=True, slots=True)
class RoomChat(Command):
    text: str


@dataclass(frozen=True, slots=True)
class OpenFreeEntryVote(Command):
    pass


@dataclass(frozen=True, slots=True)
class VoteFreeEntry(Command):
    approve: bool


# ---- 系统命令(origin = None)----


@dataclass(frozen=True, slots=True)
class Connect(Command):
    """握手后接入大厅;若 nick 已在 world.users(OFFLINE)→ 重连恢复。"""

    nick: str


@dataclass(frozen=True, slots=True)
class Disconnect(Command):
    nick: str


@dataclass(frozen=True, slots=True)
class Timeout(Command):
    """行动超时;epoch 为调度时快照,reduce 进门比对 hand.epoch 做 staleness。"""

    nick: str
    epoch: int


@dataclass(frozen=True, slots=True)
class Cleanup(Command):
    """占座到期清理(OFFLINE 超 liveness)。"""

    nick: str
