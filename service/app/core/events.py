"""reduce 的产出 `Event`(见 core.md「事件产出一览」)。

A 组(走 per-connection Sender 队列对外发):Broadcast / Personal。
B 组(同步交给 Timer,本地快操作不入队):TurnChanged / ClearAction。
落库:Persist(快照值交 delayDB,绝不持 world 活引用 —— 工作副本天然保证)。

`msg` payload 是 wire ServerMessage(P6,wire.md);P0 阶段先以基类占位,
具体消息清单与 codegen 在 P6 落地。Persist 的 payload 是 delayDB 结构(P4,db.md)。
"""

from dataclasses import dataclass


class ServerMessage:
    """wire 出站消息基类(占位)。P6 落地可辨识联合 ClientMessage/ServerMessage。"""


class PersistPayload:
    """delayDB 写入项基类(占位)。P4 落地 PointsWrite(状态写)/ HandRecordWrite(事件写)。"""


@dataclass(frozen=True, slots=True)
class Event:
    pass


# ---- A 组:对外(经 Sender 队列)----


@dataclass(frozen=True, slots=True)
class Broadcast(Event):
    """发给某房间全体在房用户;dispatch 时若房已销毁需 rooms.get 容错(connection.md)。"""

    room: str
    msg: ServerMessage


@dataclass(frozen=True, slots=True)
class Personal(Event):
    """私发给单个 nick(回执 / HoleCards / StateSnapshot / 离开者 UserLeft)。"""

    nick: str
    msg: ServerMessage


# ---- B 组:内部(同步交 Timer)----


@dataclass(frozen=True, slots=True)
class TurnChanged(Event):
    """换行动者:Timer 起/重置该房行动倒计时。epoch 供超时 staleness 判据(core.md)。"""

    room: str
    epoch: int


@dataclass(frozen=True, slots=True)
class ClearAction(Event):
    """停该房行动倒计时(手结束)。"""

    room: str


# ---- 落库 ----


@dataclass(frozen=True, slots=True)
class Persist(Event):
    """交 delayDB 异步落库的快照(状态写覆盖 / 事件写追加,见 db.md)。"""

    payload: PersistPayload
