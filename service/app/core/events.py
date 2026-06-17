from dataclasses import dataclass


class ServerMessage:
    """wire 出站消息基类(P6 落地)。"""


class PersistPayload:
    """delayDB 写入项基类(P4 落地)。"""


@dataclass(frozen=True, slots=True)
class Event:
    pass


@dataclass(frozen=True, slots=True)
class Broadcast(Event):
    room: str  # 发给该房间全体在房成员
    msg: ServerMessage  # wire 报文(投影时已按隐私裁剪)


@dataclass(frozen=True, slots=True)
class Personal(Event):
    nick: str  # 单一收件人(底牌 / StateSnapshot / UserLeft 回执)
    msg: ServerMessage  # 只发给该 nick 的 wire 报文


@dataclass(frozen=True, slots=True)
class TurnChanged(Event):
    room: str  # 要(重)起行动倒计时的房间
    acting_nick: str  # 新行动者;Timer 据它构造 Timeout(nick, epoch)
    epoch: int  # 当前 hand.epoch,供 Timeout staleness


@dataclass(frozen=True, slots=True)
class ClearAction(Event):
    room: str  # 停该房行动倒计时(手结束)


@dataclass(frozen=True, slots=True)
class Persist(Event):
    payload: PersistPayload  # 交 delayDB 的快照值(状态写 / 事件写)
