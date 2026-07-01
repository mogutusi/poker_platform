# reduce 摊牌/结束产出的 delayDB 事件写载荷(Persist.payload),与「出站消息」(messages.py)分开。
#
# 临时性(P1):同 messages.py 的临时载荷——wire/db(P6/P4)未落地前用纯 frozen dataclass 承载
# 快照值,使 reduce 可纯单测;P4 由对齐 ORM 的 HandRecordWrite 取代/对齐(见 db.md「Persist 接口」)。
# 隐私(core.md 不变量 3):记录只存**结果**(uid + 初始/最终筹码 + 池额),绝不含底牌/牌堆。

from dataclasses import dataclass
from datetime import datetime

from app.core.events import PersistPayload


@dataclass(frozen=True)
class ParticipantWrite:
    uid: int  # 不可变账号主键(= UserState.uid);记录按它,不按可变 nickname
    initial_points: int  # 开局锁入本手的筹码(in_game_points 快照)
    final_points: int  # 结算后还回座位的筹码


@dataclass(frozen=True)
class PointsWrite(PersistPayload):
    # 全局积分状态写(买入扣 / 离桌·清理退);按不可变 uid 落库,delayDB 同键覆盖只落最新(见 user.md/db.md)。
    uid: int  # 不可变账号主键(= UserState.uid);落库按它,不按可变 nickname
    points: int  # 该账号当前全局积分余额(全量快照值,非增量)


@dataclass(frozen=True)
class HandRecordWrite(PersistPayload):
    dedupe_key: str  # f"{room}:{hand.seq}";delayDB 幂等键(见 db.md / core.md 手牌标识)
    room: str  # 房名(= work.room_name);denormalized 供 HandRecord.room 列过滤,免从 dedupe_key 解析(见 changes/0052)
    start_time: datetime  # 开局墙钟(shell 经 StartHand 带入,core 只携带、不读时钟)
    final_pot: int  # 本手各子池金额之和(不含退还的未叫注)
    participants: tuple[ParticipantWrite, ...]  # 每个在局玩家的 uid + 初始/最终筹码;不含底牌
    end_time: datetime | None = None  # 手结束墙钟;core 留 None(不读钟),shell 在 dispatch 盖(见 db.md / changes/0028)
