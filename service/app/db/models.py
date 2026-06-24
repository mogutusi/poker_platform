# 新架构持久化模式(SQLModel ORM,Alembic 事实源)。表定义对齐 core/records.py 的 delayDB Write 载荷:
# PointsWrite → User.points(状态写覆盖)、HandRecordWrite → HandRecord、ParticipantWrite → HandParticipant。
# 隐私(core.md 不变量 3):只存结果(uid + 初/末筹码 + 池额),绝不含底牌/牌堆。改模型 → 新迁移见 docs/db-migrations.md。

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    # 账号:uid = 不可变主键(= UserState.uid;记录/积分按它,不按可变 nickname)。
    id: Optional[int] = Field(default=None, primary_key=True)  # uid;自增主键
    nickname: str = Field(max_length=50, unique=True, index=True)  # 可变显示名(仅大厅可改)
    points: int = Field(default=0)  # 全局积分余额;delayDB PointsWrite 状态写按 uid UPSERT 覆盖此列
    # 国密鉴权列(salt/rounds/hash_password/K_user)随 P5 以新迁移加(见 docs/db-migrations.md「改模型 → 新迁移」)


class HandRecord(SQLModel, table=True):
    # 一手牌结果记录(事件写,追加;对齐 core.records.HandRecordWrite)。
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键
    dedupe_key: str = Field(max_length=128, unique=True, index=True)  # = f"{room}:{seq}";幂等 INSERT 唯一键
    start_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))  # 开局墙钟(shell 经 StartHand 带入)
    end_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))  # 手结束墙钟(shell 派发 Persist 时盖)
    final_pot: int  # 各子池金额之和(不含退还的未叫注)


class HandParticipant(SQLModel, table=True):
    # 一手牌的一个参与者(对齐 core.records.ParticipantWrite);(hand_id, uid) 复合主键 = 一手内一人一行。
    hand_id: int = Field(foreign_key="handrecord.id", primary_key=True)  # 所属手牌(FK → handrecord.id)
    uid: int = Field(foreign_key="user.id", primary_key=True)  # 参与者不可变账号主键(FK → user.id)
    initial_points: int  # 开局锁入本手的筹码(in_game_points 快照)
    final_points: int  # 结算后还回座位的筹码
