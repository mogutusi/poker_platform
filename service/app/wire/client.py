# wire 入站协议(ClientMessage):单一事实源(治理见 docs/wire.md)。
#
# 每条报文 1:1 映射成一个 core Command(系统命令 Timeout/Cleanup/Connect/Disconnect 无报文,由 shell 产生)。
# 身份不进报文(wire.md 形状 #5):Receiver 收帧后由 to_command 盖 origin=会话 nick;墙钟由 shell 盖 now
# (core 不读钟,见 commands.StartHand.started_at)。字段名对齐 core 命令,使映射平凡(见 changes/0017)。

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core import commands
from app.core.commands import Command
from app.core.enums import PlayerActionType, UserStatus


class ClientMessage(BaseModel):
    """所有入站报文的基类;具体报文带 `type` 字面量构成可辨识联合(wire.md 形状 #1)。"""

    model_config = ConfigDict(frozen=True)


class SitDown(ClientMessage):
    type: Literal["sit_down"] = "sit_down"
    seat: int  # 要入座的座位号
    wait_for_big_blind: bool = False  # 入局方式:True=等大盲免费、False=付盲即玩(默认,见 rules.md ①)


class BuyIn(ClientMessage):
    type: Literal["buy_in"] = "buy_in"
    seat: int  # 要充值的座位
    amount: int  # 从全局积分转入座位筹码的额度


class SetUserStatus(ClientMessage):
    type: Literal["set_user_status"] = "set_user_status"
    status: UserStatus  # 请求的新状态
    seat: int | None = None  # 涉及就座时的目标座位


class SetSmallBlind(ClientMessage):
    type: Literal["set_small_blind"] = "set_small_blind"
    amount: int  # 新小盲额(任何在房成员配置,无房主;大盲 = 2× 派生;上下限由 shell 按 gameconfig 防护)


class SetBuyIn(ClientMessage):
    type: Literal["set_buy_in"] = "set_buy_in"
    amount: int  # 新房间默认买入额(任何在房成员配置,无房主;上下限由 shell 按 gameconfig 防护)


class LeaveRoom(ClientMessage):
    type: Literal["leave_room"] = "leave_room"
    # 无参数:退房目标房由 world.users[origin].room 推定(身份不进报文)


class StartHand(ClientMessage):
    type: Literal["start_hand"] = "start_hand"
    seat: int  # 发起人座位(started_at 由 shell 盖、deck 生产恒空,皆不进报文)


class PlayerAction(ClientMessage):
    type: Literal["player_action"] = "player_action"
    action: PlayerActionType  # FOLD / CHECK / BET
    bet_amount: int | None = None  # 本街目标总额(BET 时必填;数值合法性由 core betting 校验)


class RoomChat(ClientMessage):
    type: Literal["room_chat"] = "room_chat"
    text: str  # 房间聊天正文(目标房由 world.users[origin].room 推定;非空/长度/限速由 shell 防护)


class OpenFreeEntryVote(ClientMessage):
    type: Literal["open_free_entry_vote"] = "open_free_entry_vote"
    # 无参数:为当前 new_here 玩家开一次免盲投票(候选/投票人由房间状态推定,见 rules.md ①)


class VoteFreeEntry(ClientMessage):
    type: Literal["vote_free_entry"] = "vote_free_entry"
    approve: bool  # 该投票人对免盲的表态(全票 approve 才免;任一 reject 即失败)


class JoinRoom(ClientMessage):
    type: Literal["join_room"] = "join_room"
    room: str  # 目标房名;uid/loaded 不进报文——Receiver 按连接 nick 读 DB 富化(见 changes/0030 决策 1)


class FetchRoomChat(ClientMessage):
    type: Literal["fetch_room_chat"] = "fetch_room_chat"
    room: str  # 要拉历史的房名;免去读 world 解析「他在哪」,也允许拉任意房的历史(同 JoinRoom),走 shell 直服务(见 changes/0036/0071)


class DirectMessage(ClientMessage):
    type: Literal["direct_message"] = "direct_message"
    to_nick: str  # 收件人昵称(shell 解析 uid + 在线判断;不存在→DMUndelivered);发件人身份不进报文,取连接 nick
    text: str  # 私信正文(非空/长度/限速由 shell 防护;不含 hole_cards/deck);走 shell 路由不进 reduce(见 changes/0038)


class DMMarkRead(ClientMessage):
    type: Literal["dm_mark_read"] = "dm_mark_read"
    peer_nick: str  # 对端昵称(把和 ta 的会话标读;读者身份不进报文,取连接 nick);不存在→error
    read_through: datetime  # 读到此刻为止(含);客户端回传(源自收到的 DMDelivered.created_at);走 shell 路由(见 changes/0039)


# codegen 注册表 + parse 可辨识联合的成员(scripts/gen_wire_ts.py 据此生成);新增报文须登记。
CLIENT_MESSAGES: tuple[type[ClientMessage], ...] = (
    SitDown,
    BuyIn,
    SetUserStatus,
    SetSmallBlind,
    SetBuyIn,
    LeaveRoom,
    StartHand,
    PlayerAction,
    RoomChat,
    OpenFreeEntryVote,
    VoteFreeEntry,
    JoinRoom,
    FetchRoomChat,
    DirectMessage,
    DMMarkRead,
)

_ClientMessageUnion = Annotated[
    Union[
        SitDown, BuyIn, SetUserStatus, SetSmallBlind, SetBuyIn, LeaveRoom, StartHand, PlayerAction, RoomChat, OpenFreeEntryVote, VoteFreeEntry, JoinRoom, FetchRoomChat, DirectMessage, DMMarkRead
    ],
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(_ClientMessageUnion)


def parse(data: str | bytes) -> ClientMessage:
    # 解析入站帧载荷(明文 JSON)为 ClientMessage;非法 type/字段由 Pydantic 抛 ValidationError,
    # 调用方(Receiver)兜成 ErrorMessage(见 wire.md / connection.md)。
    return _ADAPTER.validate_json(data)


def to_command(msg: ClientMessage, origin: str, now: datetime) -> Command:
    # ClientMessage → Command:盖 origin(会话身份,不信报文)+ now(shell 墙钟,仅 StartHand 用)。
    match msg:
        case SitDown():
            return commands.SitDown(origin=origin, seat=msg.seat, wait_for_big_blind=msg.wait_for_big_blind)
        case BuyIn():
            return commands.BuyIn(origin=origin, seat=msg.seat, amount=msg.amount)
        case SetUserStatus():
            return commands.SetUserStatus(origin=origin, status=msg.status, seat=msg.seat)
        case SetSmallBlind():
            # 纯映射;实际收发路径里 Receiver 先在 `_guard_room_config` 按 gameconfig 上下限防护(0043),
            # 不经此分支(同 RoomChat)。保留作通用映射 + 协议直测;reduce `_set_small_blind` 仍兜 ≤0 + 授权/时机。
            return commands.SetSmallBlind(origin=origin, amount=msg.amount)
        case SetBuyIn():
            return commands.SetBuyIn(origin=origin, amount=msg.amount)
        case LeaveRoom():
            return commands.LeaveRoom(origin=origin)
        case StartHand():
            return commands.StartHand(origin=origin, seat=msg.seat, started_at=now)
        case PlayerAction():
            return commands.PlayerAction(origin=origin, action=msg.action, bet_amount=msg.bet_amount)
        case RoomChat():
            # 纯映射;实际收发路径里 Receiver 先在 `_guard_room_chat` 拦 RoomChat 做文本防护 + 限速(0033),
            # 不经此分支。保留作通用映射 + 协议直测;reduce `_room_chat` 仍只读、不重校验文本(0021)。
            return commands.RoomChat(origin=origin, text=msg.text)
        case OpenFreeEntryVote():
            return commands.OpenFreeEntryVote(origin=origin)
        case VoteFreeEntry():
            return commands.VoteFreeEntry(origin=origin, approve=msg.approve)
        case JoinRoom():
            # JoinRoom 需 DB 富化 uid/loaded(身份/积分不进报文),由 Receiver 异步读 DB 构造,不经此(见 changes/0030 决策 1)。
            raise AssertionError("JoinRoom 须由 Receiver 经 DB 富化构造,不走 to_command")
        case FetchRoomChat():
            # FetchRoomChat 走 shell 直服务(读房聊环形缓冲回 outbound),不映射 Command、不进 reduce(见 changes/0036)。
            raise AssertionError("FetchRoomChat 走 shell 路由,不走 to_command")
        case DirectMessage():
            # 私信走 shell 路由(messaging.md §私信):解析 uid + 落库 DMWrite + 在线投 DMDelivered,不进 reduce(见 changes/0038)。
            raise AssertionError("DirectMessage 走 shell 路由,不走 to_command")
        case DMMarkRead():
            # 标记已读走 shell 路由(messaging.md §私信):put 已读游标 + 在线回执 DMRead,不进 reduce(见 changes/0039)。
            raise AssertionError("DMMarkRead 走 shell 路由,不走 to_command")
    raise AssertionError(f"unmapped client message: {type(msg).__name__}")  # 不可达:CLIENT_MESSAGES 穷尽
