# wire 出站协议(ServerMessage):单一事实源(治理见 docs/wire.md)。
#
# reduce 投影域状态时**直接构造**这些 Pydantic DTO 作为 Broadcast/Personal 的 msg
# (core 可 import wire DTO,见 models.md);codegen(scripts/gen_wire_ts.py)据此生成
# frontend/src/types/wire.gen.ts,前端只消费、禁手写。
#
# 隐私(wire.md 契约 #5 / core.md 不变量 3):hole_cards/deck 只在**揭示点** DTO 显式携带——
# HoleCards(Personal 私发本人)、ShowdownReveal(HandShowDown 揭示未弃牌者);其余 DTO **结构上无**
# 此字段(结构性缺位即隐私兜底,见 changes/0017)。

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, RoomStatus, UserStatus
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


class SeatView(_Frozen):
    seat_position: int  # 座位号
    nickname: str  # 占座者
    status: UserStatus  # 该占座者在房状态(SITTING_IN/READY_TO_PLAY/PLAYING/SITTING_OUT/OFFLINE)
    points: int  # 当前可用筹码:在手时=本手剩余 Player.points,不在手时=Seat.points(seats 为「筹码后手」单一源)
    new_here: bool  # 下一手是否需付盲入局(防躲盲;见 rules.md ①)


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


class UserJoined(ServerMessage):
    type: Literal["user_joined"] = "user_joined"
    nickname: str  # 新进房者(进房即 WATCHING 观战;Broadcast 给全房,见 lobby.md)


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


class RoomConfigChanged(ServerMessage):
    # 房间参数被 0 号位占座者改动后广播给全房(含观战者),客户端据此更新桌面注码/买入默认值(见 changes/0043)。
    # 携完整当前配置快照(不止改动项)——客户端无需累积、单条即对齐;房配不落库(storage.md),重启回 gameconfig 缺省。
    type: Literal["room_config_changed"] = "room_config_changed"
    small_blind: int  # 当前小盲额
    big_blind: int  # 当前大盲额(= 2×小盲,派生非存储)
    buy_in: int  # 当前房间默认买入额


class StateSnapshot(ServerMessage):
    # 进房/重连私发(Personal):一次性对齐整桌当前态。逐收件人构造——your_hole_cards 仅自己的底牌,
    # 在手玩家投影为 players(PlayerView 结构上无 hole_cards ⇒ 他人底牌不泄露,见 wire.md 隐私)。
    type: Literal["state_snapshot"] = "state_snapshot"
    room: str  # 房间名
    max_seats: int  # 座位总数(渲染空位:seats 只列已占座,各带 seat_position)
    button_position: int  # 庄家座位
    small_blind: int  # 小盲额
    big_blind: int  # 大盲额(= 2×小盲)
    buy_in: int  # 房间默认买入额(重连也能拿到当前值;SetBuyIn 改后随快照对齐,见 changes/0043)
    room_status: RoomStatus  # PENDING_START / HAND_STARTED
    seats: tuple[SeatView, ...]  # 仅已占座位(各带 seat_position;空座由 max_seats 推)
    watchers: tuple[str, ...]  # 在房观战者(无座位)nick
    hand_status: HandStatus | None  # 进行中手牌的街;无手为 None
    board: tuple[Card, ...]  # 已发公共牌;无手为空
    pot: int  # 总底池(contributed + 各人本街 bet_amount);无手为 0
    acting_position: int | None  # players 下标:当前行动者;无手/无人可行动为 None
    players: tuple[PlayerView, ...]  # 行动序在手玩家(不含底牌);无手为空
    your_hole_cards: tuple[Card, Card] | None  # 收件人自己的底牌(仅其在手时);他人底牌结构性缺位


class ChatMessage(ServerMessage):
    type: Literal["chat_message"] = "chat_message"
    from_nick: str  # 发言者(取连接绑定身份,不信报文自报)
    text: str  # 聊天正文;不含游戏隐私(hole_cards/deck),结构上无此字段(messaging.md 脱敏红线)


class RoomChatHistory(ServerMessage):
    type: Literal["room_chat_history"] = "room_chat_history"
    room: str  # 历史所属房名(= 请求的房;客户端据它对号入座)
    messages: tuple[ChatMessage, ...]  # 该房最近 N 条房聊(旧→新);shell 直发,不进 reduce(见 changes/0036)


class DMDelivered(ServerMessage):
    # 私信投递给收件人(messaging.md §私信):在线实时投 / 登录补收(0040)均用此形,前端按 msg_id 去重对齐。
    type: Literal["dm_delivered"] = "dm_delivered"
    msg_id: str  # 私信幂等键(= DMWrite.dedupe_key);前端去重 / 引用 / 跨实时与补收对齐
    from_nick: str  # 发件人(发件连接绑定身份,不信报文自报)
    text: str  # 私信正文;结构上无 hole_cards/deck(脱敏红线)
    created_at: datetime  # 服务端盖墙钟(展示时间 + 未读/已读比较键,见 messaging.md / db.md);JSON 序列化为 ISO 串


class DMUndelivered(ServerMessage):
    # 私信投递硬失败回发件人——仅「对端根本不存在」这种错(messaging.md §私信)。离线不算:落库未读、登录补收。
    type: Literal["dm_undelivered"] = "dm_undelivered"
    to_nick: str  # 投递失败的目标昵称;前端据它把该条外发标失败


class DMRead(ServerMessage):
    # 已读回执回发件人(messaging.md §私信):「reader 把我发给 ta 的消息读到了 read_through」。在线实时 / 登录补收(0040)同形。
    type: Literal["dm_read"] = "dm_read"
    reader_nick: str  # 把消息读到 read_through 的人(= 原收件人)
    read_through: datetime  # 对方已读到此刻为止(含);JSON 序列化为 ISO 串


class FreeEntryVoteUpdated(ServerMessage):
    type: Literal["free_entry_vote_updated"] = "free_entry_vote_updated"
    candidates: tuple[str, ...]  # 受这次入局盲影响的 new_here 玩家(通过则免费入局)
    voters: tuple[str, ...]  # 合格投票人 nick(已入局且 READY_TO_PLAY;全票通过才免)
    approvals: tuple[str, ...]  # 当前已 approve 的投票人(开票为空,逐票累加)


class FreeEntryVoteClosed(ServerMessage):
    type: Literal["free_entry_vote_closed"] = "free_entry_vote_closed"
    passed: bool  # True=全票通过、False=被否决 / 失败
    waived: tuple[str, ...]  # 通过时本手免费入局的玩家快照;失败为空


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
    UserJoined,
    UserLeft,
    PlayerBoughtIn,
    RoomConfigChanged,
    StateSnapshot,
    ChatMessage,
    RoomChatHistory,
    DMDelivered,
    DMUndelivered,
    DMRead,
    FreeEntryVoteUpdated,
    FreeEntryVoteClosed,
    ErrorMessage,
)
