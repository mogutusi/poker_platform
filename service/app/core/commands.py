from dataclasses import dataclass
from datetime import datetime

from app.core.cards import Card
from app.core.enums import PlayerActionType, UserStatus


@dataclass(frozen=True, slots=True)
class Command:
    origin: str | None  # 发起命令的 nick(= 错误回发给谁);系统命令为 None


@dataclass(frozen=True, slots=True)
class RoomCreate:
    # 建房配置:JoinRoom 到不存在的房时用它建房(见 core.md 房间生命周期)。由 shell 从 gameconfig 盖(core 不 import config);
    # 边界(seats≥2 / 正额)由 gameconfig Field 兜。加入已存在房时忽略此配置。
    small_blind: int  # 新房小盲(大盲 = 2× 派生)
    buy_in: int  # 新房默认买入额(Room.buy_in)
    seats: int  # 新房座位数(Room.seats 长度)
    chat_history_size: int  # 新房房聊环形历史条数(Room.chat_history 的 maxlen;shell 从 ROOM_CHAT_HISTORY_SIZE 盖,0071)


@dataclass(frozen=True, slots=True)
class JoinRoom(Command):
    room: str  # 目标房(唯一带 room 的命令)
    uid: int  # shell 从 DB 读出的账号主键
    loaded: int  # shell 从 DB 读出的该账号当前全局积分
    create: RoomCreate | None = None  # 房不存在时建房配置(shell 盖);None = 不带 → 房必须已存在,否则 NO_SUCH_ROOM


@dataclass(frozen=True, slots=True)
class LeaveRoom(Command):
    pass


@dataclass(frozen=True, slots=True)
class SitDown(Command):
    seat: int  # 要入座的座位号
    wait_for_big_blind: bool = False  # 入局方式:True=等大盲免费、False=付盲即玩(默认,见 rules.md ①)


@dataclass(frozen=True, slots=True)
class BuyIn(Command):
    seat: int  # 要充值的座位
    amount: int  # 从全局积分转入座位筹码的额度


@dataclass(frozen=True, slots=True)
class SetUserStatus(Command):
    status: UserStatus  # 请求的新状态
    seat: int | None = None  # 涉及就座时的目标座位


@dataclass(frozen=True, slots=True)
class SetSmallBlind(Command):
    amount: int  # 新小盲额(任何在房成员配置,无房主;见 changes/0044)


@dataclass(frozen=True, slots=True)
class SetBuyIn(Command):
    amount: int  # 新房间买入额(任何在房成员配置,无房主;见 changes/0044)


@dataclass(frozen=True, slots=True)
class StartHand(Command):
    seat: int  # 发起人座位
    started_at: datetime  # shell 盖好的墙钟,带入 Hand.start_time
    deck: list[Card] | None = None  # 测试/重放注入;None → SystemRandom 洗牌


@dataclass(frozen=True, slots=True)
class PlayerAction(Command):
    action: PlayerActionType  # FOLD / CHECK / BET
    bet_amount: int | None = None  # 本街目标总额(BET 时必填)


@dataclass(frozen=True, slots=True)
class RoomChat(Command):
    text: str  # 聊天内容;只读命令,产出 Broadcast(ChatMessage)


@dataclass(frozen=True, slots=True)
class OpenFreeEntryVote(Command):
    pass


@dataclass(frozen=True, slots=True)
class VoteFreeEntry(Command):
    approve: bool  # 该投票人对免盲的表态


@dataclass(frozen=True, slots=True)
class Connect(Command):
    nick: str  # 接入的 nick;已在 world.users(OFFLINE)则为重连


@dataclass(frozen=True, slots=True)
class Disconnect(Command):
    nick: str  # 断开的 nick;在房则标 OFFLINE


@dataclass(frozen=True, slots=True)
class Timeout(Command):
    # 身份三元组 (room, hand_seq, epoch):进门三项全等才算新鲜(见 timer.md 过期防护 / changes/0090)。
    # 单靠 epoch 不够——它每手从 0 起,上一手和这一手会重号;而 seq 只在房内单调,两个房的第 1 手同为 1。
    nick: str  # 轮到谁超时(游戏目标,非错误收件人)
    room: str  # 排这条队时的房名。**不用于路由**(目标房照旧由 world.users[nick].room 解析),只作校验:
    # 解析出来的房与它不符 ⇒ 这是别的房排的队(人已换房),忽略
    hand_seq: int  # 调度时的 hand.seq 快照(房内单调);挡跨手撞号
    epoch: int  # 调度时的 hand.epoch 快照;挡本手内的回合推进


@dataclass(frozen=True, slots=True)
class Cleanup(Command):
    nick: str  # 占座到期者(OFFLINE 超过 liveness)
