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
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, UserStatus
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


@dataclass(frozen=True)
class PlayerActed(ServerMessage):
    seat_position: int  # 行动者座位
    nickname: str  # 行动者
    action: PlayerActionType  # FOLD / CHECK / BET
    bet_amount: int  # 行动后本人本街投入(快照于街结算清零前)
    points: int  # 行动后本人剩余筹码
    status: PlayerStatus  # 行动后本人状态(ACTIVE / FOLDED / ALLIN)
    last_bet: int  # 推进后本街需跟到的额度(进新街为 0)
    pot: int  # 推进后总底池(contributed + 各人本街 bet_amount)
    acting_position: int | None  # 推进后下一行动者(players 下标);手牌结束为 None


@dataclass(frozen=True)
class ShowdownReveal:
    seat_position: int  # 摊牌者座位
    nickname: str  # 摊牌者
    hole_cards: tuple[Card, Card]  # 隐私揭示:仅摊牌(HandShowDown)合法公开未弃牌者底牌


@dataclass(frozen=True)
class HandShowDown(ServerMessage):
    board: tuple[Card, ...]  # 完整 5 张公共牌
    reveals: tuple[ShowdownReveal, ...]  # 未弃牌者底牌(底牌唯一合法公开点,见 core.md 不变量 3)


@dataclass(frozen=True)
class NickAmount:
    nickname: str  # 收款者
    amount: int  # 金额(赢得 / 退还)


@dataclass(frozen=True)
class HandEnded(ServerMessage):
    winnings: tuple[NickAmount, ...]  # 各赢家从子池赢得
    refunds: tuple[NickAmount, ...]  # 未叫注退还(及退化无主池退回)


@dataclass(frozen=True)
class UserStatusChanged(ServerMessage):
    nickname: str  # 状态变更者
    status: UserStatus  # 新 UserStatus(如 OFFLINE / SITTING_OUT / READY_TO_PLAY)
    seat_position: int | None  # 占座者的座位号;未就座(大厅/观战)为 None


@dataclass(frozen=True)
class UserLeft(ServerMessage):
    nickname: str  # 离房者;Broadcast 给留下者、Personal 回执给本人(见 connection.md/lobby.md)
    seat_position: int | None  # 离开时释放的座位号;未就座者为 None
