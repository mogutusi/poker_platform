# reduce 投影域状态产出的出站载荷(Broadcast/Personal 的 msg)。
#
# 临时性(P1):wire(P6)未落地前,这些用纯 frozen dataclass 承载语义快照,只装 reduce
# 算得出的字段,使 reduce 可在无 wire/codegen 下纯单测(见 testing.md)。P6 由 wire 的
# Pydantic 可辨识联合 DTO 取代/对齐(见 wire.md / models.md);投影点集中在 reduce,替换面可控。
#
# 隐私(core.md 不变量 3 / wire.md):底牌/牌堆只在 HoleCards(Personal)与摊牌出现;
# 其余载荷(HandStarted/HandStatusChanged/PlayerView)结构上**不含** hole_cards/deck 字段。

from dataclasses import dataclass

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerStatus
from app.core.events import ServerMessage


@dataclass(frozen=True)
class PlayerView:
    seat_position: int  # 座位号
    nickname: str  # 占座者
    points: int  # 本手剩余可下注筹码(锁入后)
    bet_amount: int  # 本街已投入(含盲注 / 入局 post)
    status: PlayerStatus  # ACTIVE / FOLDED / ALLIN


@dataclass(frozen=True)
class HandStarted(ServerMessage):
    hand_seq: int  # 本手房内单调标识(= room.hand_seq)
    button_position: int  # 本手庄家座位
    small_blind: int  # 小盲额
    big_blind: int  # 大盲额(= 2×小盲)
    players: tuple[PlayerView, ...]  # 行动序座位快照([0]=SB、[1]=BB);不含底牌
    acting_position: int | None  # players 下标:preflop 首行动者;无人可行动为 None


@dataclass(frozen=True)
class HoleCards(ServerMessage):
    cards: tuple[Card, Card]  # 隐私:仅 Personal 私发本人,不进 Broadcast/日志/落库


@dataclass(frozen=True)
class HandStatusChanged(ServerMessage):
    status: HandStatus  # 当前街(开局为 PRE_FLOP)
    board: tuple[Card, ...]  # 已发公共牌;PRE_FLOP 为空,逐街追加
