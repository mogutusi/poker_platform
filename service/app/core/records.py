# reduce 摊牌/结束产出的 delayDB 事件写载荷(Persist.payload),与「出站消息」(messages.py)分开。
#
# 临时性(P1):同 messages.py 的临时载荷——wire/db(P6/P4)未落地前用纯 frozen dataclass 承载
# 快照值,使 reduce 可纯单测;P4 由对齐 ORM 的 HandRecordWrite 取代/对齐(见 db.md「Persist 接口」)。
# 隐私(core.md 不变量 3,changes/0110 修订):记录存结果(uid + 初始/最终筹码 + 池额)+ 公共牌 + 每人底牌,
# **牌堆仍不落库**。底牌落库是为了手牌历史里能看自己那手;读侧(rest/hands.py)只把底牌给本人,
# 或给摊牌亮过牌的人(showed)——和牌桌上能看到的一致,别人没亮的牌永远出不了服务器。

from dataclasses import dataclass
from datetime import datetime

from app.core.events import PersistPayload


@dataclass(frozen=True)
class ParticipantWrite:
    uid: int  # 不可变账号主键(= UserState.uid);记录按它,不按可变 nickname
    initial_points: int  # 开局锁入本手的筹码(in_game_points 快照)
    final_points: int  # 结算后还回座位的筹码
    seat_position: int | None = None  # 本手所坐的座位(手牌历史里推算 BTN/SB/BB… 位置用)
    hole_cards: str | None = None  # 底牌 treys 串,如 "AsKd";隐私:读侧只给本人 / 摊牌亮过的人
    vpip: bool = False  # 翻牌前是否自愿入池(统计 VPIP 用)
    showed: bool = False  # 是否在摊牌时亮了牌


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
    board: str = ""  # 公共牌 treys 串连写(0–5 张,如 "Ah7c2dTsKs");公开信息
    button_position: int | None = None  # 本手庄位座位号
    big_blind: int | None = None  # 本手大盲额(统计 bb/100 用)
    actions: tuple[tuple[str, int, str, int], ...] = ()  # 动作流水 (街, uid, 动作, 本街目标额);0111,统计用
