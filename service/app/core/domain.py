"""core 的权威域模型(`world`)。

可变 dataclass:reduce 原地改 GameLoop 给的**工作副本**,成功 commit、失败丢弃
(见 storage.md)。与 wire DTO / DB ORM 分离(三套表示见 models.md)。

不变量(core.md):
- core 纯同步,这里只放数据 + 极简纯方法,不 import IO/框架/gameconfig。
- 底牌(`Player.hole_cards`)与牌堆(`Hand.deck`)是隐私:除 Personal(HoleCards)
  与摊牌 HandShowDown 外,任何事件/日志/落库都不得包含。
- 配置默认值(座位数/盲注/买入)不在此烤死,由 shell/lobby 构造时传入。
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus


@dataclass
class UserState:
    """全局用户在内存中的权威态(积分 + 所在房间)。键是可变的 nickname,
    但落库一律按不可变的 `uid`(= DB User.id),见 user.md / 0001。"""

    uid: int
    nickname: str
    points: int
    room: str | None = None  # 当前所在房间;None = 在大厅。一个人只在一个房间(不变量 8)


@dataclass
class Player:
    """「在这一手里」某座位的状态;手牌结束即弃。"""

    nickname: str
    seat_position: int
    points: int  # 本手剩余可下注筹码
    status: PlayerStatus = PlayerStatus.ACTIVE
    bet_amount: int = 0  # 本街已投入(街结束并入 contributed、清零)
    has_acted: bool = False  # 本街是否已自愿行动(街开始 / 被加注重开时置 False),见 rules.md ②
    hole_cards: tuple[Card, Card] | None = None  # 隐私字段


@dataclass
class Hand:
    """一手牌的全部状态。players 按行动顺序排列:players[0]=小盲、players[1]=大盲
    (heads-up 时 players[0]=button=小盲),见 rules.md ①。"""

    status: HandStatus
    players: list[Player]
    seq: int  # = 开局时 room.hand_seq,房间内单调;dedupe_key = f"{room}:{seq}"
    start_time: datetime  # 开局墙钟,由 shell 盖入;core 只存不读(不变量 1)
    acting_position: int | None = None  # players[acting_position] = 当前行动者
    last_bet: int = 0  # 本街需跟到的额度
    last_raise_size: int = 0  # 最近一次加注的增量,供 min-raise(rules.md ②)
    deck: list[Card] = field(default_factory=list)  # 隐私字段:未发的牌堆
    contributed: dict[str, int] = field(default_factory=dict)  # nick → 本手累计投入
    flop: tuple[Card, Card, Card] | None = None
    turn: Card | None = None
    river: Card | None = None
    epoch: int = 0  # 行动推进计数;Timeout staleness 判据(core.md)


@dataclass
class Seat:
    """「在桌」的钱与身份,跨手牌存活。"""

    nickname: str
    points: int  # 桌上可用筹码(不在手牌里时)
    in_game_points: int = 0  # 手牌进行中被锁进 Hand 的本金(快照,用于结算/记录)
    new_here: bool = True  # 上一手未参与;入局需付盲即玩 / 等大盲(rules.md ①)
    wait_for_big_blind: bool = False  # 选「等大盲免费入局」而非「付盲即玩」(wire 标志)


@dataclass
class EntryVote:
    """进行中的免盲投票(rules.md ①)。approvals = 已 approve 的投票人 nick。
    任一 reject 即失败;全体投票人 approve 则把当前 new_here 快照进
    room.waive_entry_for。投票人离场时由 reduce 重算。"""

    approvals: set[str] = field(default_factory=set)
    rejected: bool = False


@dataclass
class Room:
    seats: list[Seat | None]  # 定长 = MAX_SEATS,由 shell/lobby 构造时给定
    small_blind: int
    buy_in: int
    users_in_room: dict[str, UserStatus] = field(default_factory=dict)  # 在房用户 → 状态机
    hand: Hand | None = None
    status: RoomStatus = RoomStatus.PENDING_START
    button_position: int = 0  # 庄家座位号
    hand_seq: int = 0  # 房间内手牌单调序号(开局自增取得 hand.seq)
    entry_vote: EntryVote | None = None  # 进行中的免盲投票
    waive_entry_for: set[str] = field(default_factory=set)  # 已全票免盲、下一手免费入局的快照
    leaving: set[str] = field(default_factory=set)  # 局中 LeaveRoom 已 auto-fold、待手尾驱逐(rules.md ④)


@dataclass
class World:
    """内存权威:全部房间 + 全局用户积分。GameLoop 是唯一写者。"""

    rooms: dict[str, Room] = field(default_factory=dict)
    users: dict[str, UserState] = field(default_factory=dict)  # nick → UserState
