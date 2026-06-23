from dataclasses import dataclass, field
from datetime import datetime

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus


@dataclass
class UserState:
    uid: int  # 不可变账号主键(= User.id);落库/记录按它,不按可变的 nickname
    nickname: str  # 可变显示名;也是 world.users 的键(只能在大厅改)
    points: int  # 全局积分余额,内存权威
    room: str  # 当前房间;恒有值——大厅用户只活在 ConnectionManager,不进 world.users


@dataclass
class Player:
    nickname: str  # 本手内的身份
    seat_position: int  # 占用的 Room.seats 下标
    points: int  # 本手剩余筹码(可下注额)
    status: PlayerStatus = PlayerStatus.ACTIVE  # ACTIVE / FOLDED / ALLIN
    bet_amount: int = 0  # 本街已投入;街结束并入 contributed 并清零
    has_acted: bool = False  # 本街是否已自愿行动;街开始/被加注重开时置 False
    hole_cards: tuple[Card, Card] | None = None  # 隐私:不进广播/日志/落库


@dataclass
class Hand:
    status: HandStatus  # 当前街(PRE_FLOP..ENDING)
    players: list[Player]  # 行动顺序:[0]=小盲、[1]=大盲(两人局 [0]=庄=小盲)
    seq: int  # = 开局时 room.hand_seq;房间内单调;dedupe_key = f"{room}:{seq}"
    start_time: datetime  # shell 盖入的墙钟;core 只存不读(不据墙钟分支)
    acting_position: int | None = None  # players[acting_position] = 当前行动者
    last_bet: int = 0  # 本街需跟到的额度
    last_raise_size: int = 0  # 最近一次加注的增量,供 min-raise
    deck: list[Card] = field(default_factory=list)  # 隐私:未发的牌堆
    contributed: dict[str, int] = field(default_factory=dict)  # nick → 本手累计投入
    flop: tuple[Card, Card, Card] | None = None  # 前 3 张公共牌;None = 未发
    turn: Card | None = None  # 第 4 张公共牌;None = 未发
    river: Card | None = None  # 第 5 张公共牌;None = 未发
    epoch: int = 0  # 每次行动推进/街切换自增;Timeout staleness 判据


@dataclass
class Seat:
    nickname: str  # 占座者
    points: int  # 不在手牌时桌上可用筹码
    in_game_points: int = 0  # 手牌进行中锁进 Hand 的本金(快照,用于结算/记录)
    new_here: bool = True  # 上一手未参与;入局需付盲即玩 / 等大盲
    wait_for_big_blind: bool = False  # 选「等大盲免费入局」而非「付盲即玩」(wire 标志)


@dataclass
class EntryVote:
    candidates: frozenset[str]  # 开票时冻结的 new_here 候选;同意只针对这批人——后来就座者不蹭、原候选离场则票失对象(rules.md ①)
    approvals: set[str] = field(default_factory=set)  # 已 approve 的投票人 nick
    rejected: bool = False  # 任一 reject 即失败


@dataclass
class Room:
    seats: list[Seat | None]  # 定长 = MAX_SEATS;None = 空座
    small_blind: int  # 小盲额;大盲 = 2 * small_blind
    buy_in: int  # 本房默认买入额
    users_in_room: dict[str, UserStatus] = field(default_factory=dict)  # 在房 nick → 状态机
    hand: Hand | None = None  # 当前手牌;两手之间为 None
    status: RoomStatus = RoomStatus.PENDING_START  # PENDING_START / HAND_STARTED
    button_position: int = 0  # 庄家座位号
    hand_seq: int = 0  # 房间内手牌单调计数
    entry_vote: EntryVote | None = None  # 进行中的免盲投票
    waive_entry_for: set[str] = field(default_factory=set)  # 快照:下一手免费入局的 new_here 集合
    leaving: set[str] = field(default_factory=set)  # 局中 LeaveRoom/Cleanup 已标记、待手尾结算后驱逐
    sitting_out_next: set[str] = field(default_factory=set)  # 局中请求 SITTING_OUT、延到手尾生效(留房、下手不发)


@dataclass
class World:
    rooms: dict[str, Room] = field(default_factory=dict)  # 房间名 → Room
    users: dict[str, UserState] = field(default_factory=dict)  # nick → UserState(仅在房用户)


@dataclass
class Work:
    # reduce 的工作副本:目标房 + users 表的深拷贝。reduce 原地改它,成功 commit、失败丢弃。
    # 类型属 core(reduce 的操作面);构造/落定由 shell/world 的 checkout/commit 负责(见 storage.md)。
    room_name: str | None  # 目标房键(None = 纯大厅命令,无房)
    room: Room | None  # 目标房深拷贝;None = world 中无此房(reduce 可新建)
    users: dict[str, UserState]  # 整份 users 表的深拷贝
