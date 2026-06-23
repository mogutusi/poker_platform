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


class BuyIn(ClientMessage):
    type: Literal["buy_in"] = "buy_in"
    seat: int  # 要充值的座位
    amount: int  # 从全局积分转入座位筹码的额度


class SetUserStatus(ClientMessage):
    type: Literal["set_user_status"] = "set_user_status"
    status: UserStatus  # 请求的新状态
    seat: int | None = None  # 涉及就座时的目标座位


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


# codegen 注册表 + parse 可辨识联合的成员(scripts/gen_wire_ts.py 据此生成);新增报文须登记。
CLIENT_MESSAGES: tuple[type[ClientMessage], ...] = (
    SitDown,
    BuyIn,
    SetUserStatus,
    LeaveRoom,
    StartHand,
    PlayerAction,
    RoomChat,
    OpenFreeEntryVote,
    VoteFreeEntry,
)

_ClientMessageUnion = Annotated[
    Union[
        SitDown, BuyIn, SetUserStatus, LeaveRoom, StartHand, PlayerAction, RoomChat, OpenFreeEntryVote, VoteFreeEntry
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
            return commands.SitDown(origin=origin, seat=msg.seat)
        case BuyIn():
            return commands.BuyIn(origin=origin, seat=msg.seat, amount=msg.amount)
        case SetUserStatus():
            return commands.SetUserStatus(origin=origin, status=msg.status, seat=msg.seat)
        case LeaveRoom():
            return commands.LeaveRoom(origin=origin)
        case StartHand():
            return commands.StartHand(origin=origin, seat=msg.seat, started_at=now)
        case PlayerAction():
            return commands.PlayerAction(origin=origin, action=msg.action, bet_amount=msg.bet_amount)
        case RoomChat():
            return commands.RoomChat(origin=origin, text=msg.text)
        case OpenFreeEntryVote():
            return commands.OpenFreeEntryVote(origin=origin)
        case VoteFreeEntry():
            return commands.VoteFreeEntry(origin=origin, approve=msg.approve)
    raise AssertionError(f"unmapped client message: {type(msg).__name__}")  # 不可达:CLIENT_MESSAGES 穷尽
