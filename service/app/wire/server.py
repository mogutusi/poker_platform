# wire 出站协议(ServerMessage):单一事实源(治理见 docs/wire.md)。
#
# reduce 投影域状态时**直接构造**这些 Pydantic DTO 作为 Broadcast/Personal 的 msg
# (core 可 import wire DTO,见 models.md);codegen(scripts/gen_wire_ts.py)据此生成
# frontend/src/types/wire.gen.ts,前端只消费、禁手写。
#
# 隐私(wire.md 契约 #5 / core.md 不变量 3):hole_cards/deck 只在**揭示点** DTO 显式携带——
# HoleCards(Personal 私发本人)、ShowdownReveal(HandShowDown 揭示未弃牌者);其余 DTO **结构上无**
# 此字段(结构性缺位即隐私兜底,见 changes/0017)。

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, UserStatus
from app.core.errors import Err, ErrorCode


class _Frozen(BaseModel):
    # wire DTO 一律冻结:报文是投影快照,不可变、不持域活引用(守不变量 7)。
    model_config = ConfigDict(frozen=True)


class ServerMessage(_Frozen):
    """所有出站报文的基类;具体消息带 `type` 字面量构成可辨识联合(wire.md 形状 #1)。"""


# ── 嵌套值对象(无 `type`,只作消息字段)──


class PlayerView(_Frozen):
    seat_position: int  # 座位号
    nickname: str  # 占座者
    points: int  # 本手剩余可下注筹码(锁入后)
    bet_amount: int  # 本街已投入(含盲注 / 入局 post)
    status: PlayerStatus  # ACTIVE / FOLDED / ALLIN


class ShowdownReveal(_Frozen):
    seat_position: int  # 摊牌者座位
    nickname: str  # 摊牌者
    hole_cards: tuple[Card, Card]  # 隐私揭示:仅摊牌(HandShowDown)合法公开未弃牌者底牌


class NickAmount(_Frozen):
    nickname: str  # 收款者
    amount: int  # 金额(赢得 / 退还)


# ── 出站消息(各带 `type` 字面量;字段平铺、snake_case、强类型)──


class HandStarted(ServerMessage):
    type: Literal["hand_started"] = "hand_started"
    hand_seq: int  # 本手房内单调标识(= room.hand_seq)
    button_position: int  # 本手庄家座位
    small_blind: int  # 小盲额
    big_blind: int  # 大盲额(= 2×小盲)
    players: tuple[PlayerView, ...]  # 行动序座位快照([0]=SB、[1]=BB);不含底牌
    acting_position: int | None  # players 下标:preflop 首行动者;无人可行动为 None


class HoleCards(ServerMessage):
    type: Literal["hole_cards"] = "hole_cards"
    cards: tuple[Card, Card]  # 隐私:仅 Personal 私发本人,不进 Broadcast/日志/落库


class HandStatusChanged(ServerMessage):
    type: Literal["hand_status_changed"] = "hand_status_changed"
    status: HandStatus  # 当前街(开局为 PRE_FLOP)
    board: tuple[Card, ...]  # 已发公共牌;PRE_FLOP 为空,逐街追加


class PlayerActed(ServerMessage):
    type: Literal["player_acted"] = "player_acted"
    seat_position: int  # 行动者座位
    nickname: str  # 行动者
    action: PlayerActionType  # FOLD / CHECK / BET
    bet_amount: int  # 行动后本人本街投入(快照于街结算清零前)
    points: int  # 行动后本人剩余筹码
    status: PlayerStatus  # 行动后本人状态(ACTIVE / FOLDED / ALLIN)
    last_bet: int  # 推进后本街需跟到的额度(进新街为 0)
    pot: int  # 推进后总底池(contributed + 各人本街 bet_amount)
    acting_position: int | None  # 推进后下一行动者(players 下标);手牌结束为 None


class HandShowDown(ServerMessage):
    type: Literal["hand_show_down"] = "hand_show_down"
    board: tuple[Card, ...]  # 完整 5 张公共牌
    reveals: tuple[ShowdownReveal, ...]  # 未弃牌者底牌(底牌唯一合法公开点,见 core.md 不变量 3)


class HandEnded(ServerMessage):
    type: Literal["hand_ended"] = "hand_ended"
    winnings: tuple[NickAmount, ...]  # 各赢家从子池赢得
    refunds: tuple[NickAmount, ...]  # 未叫注退还(及退化无主池退回)


class UserStatusChanged(ServerMessage):
    type: Literal["user_status_changed"] = "user_status_changed"
    nickname: str  # 状态变更者
    status: UserStatus  # 新 UserStatus(如 OFFLINE / SITTING_OUT / READY_TO_PLAY)
    seat_position: int | None  # 占座者的座位号;未就座(大厅/观战)为 None


class UserLeft(ServerMessage):
    type: Literal["user_left"] = "user_left"
    nickname: str  # 离房者;Broadcast 给留下者、Personal 回执给本人(见 connection.md/lobby.md)
    seat_position: int | None  # 离开时释放的座位号;未就座者为 None


class PlayerBoughtIn(ServerMessage):
    type: Literal["player_bought_in"] = "player_bought_in"
    nickname: str  # 买入者
    seat_position: int  # 充值的座位号
    amount: int  # 本次从全局积分转入的额度
    seat_points: int  # 买入后座位的可用筹码(快照)


class ErrorMessage(ServerMessage):
    type: Literal["error"] = "error"
    code: ErrorCode  # 机器可读码;前端据它映射本地化文案(wire.md 契约 #6:只回 code)
    detail: str = ""  # 开发上下文(谁/哪个座位/什么状态),非面向玩家文案;供日志/调试,前端按 code 渲染

    @classmethod
    def from_err(cls, err: Err) -> "ErrorMessage":
        # Err(core 内部信封)→ ErrorMessage(wire 报文);转换缝在 wire(见 error.md/wire.md)
        return cls(code=err.code, detail=err.detail)


# codegen 注册表(scripts/gen_wire_ts.py 据此生成 ServerMessage 可辨识联合);新增消息须登记。
SERVER_MESSAGES: tuple[type[ServerMessage], ...] = (
    HandStarted,
    HoleCards,
    HandStatusChanged,
    PlayerActed,
    HandShowDown,
    HandEnded,
    UserStatusChanged,
    UserLeft,
    PlayerBoughtIn,
    ErrorMessage,
)
