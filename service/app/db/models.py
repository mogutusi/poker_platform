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


class DMMessage(SQLModel, table=True):
    # 一条私信(事件写,追加;对齐 dm_records.DMWrite)。DB 权威(非内存权威):发即落库 = 未读,
    # 读由 0039 的游标推进;在线实时投递只是叠加其上的优化(见 messaging.md §私信)。隐私:正文不含底牌/牌堆。
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键
    dedupe_key: str = Field(max_length=128, unique=True, index=True)  # = msg_id;幂等 INSERT 唯一键
    from_uid: int = Field(foreign_key="user.id")  # 发件人不可变账号主键(FK → user.id)
    to_uid: int = Field(foreign_key="user.id", index=True)  # 收件人(FK → user.id;0039 按 to_uid 查未读,故索引)
    text: str  # 私信正文(无 max_length;单条长度由 shell DM_MAX_TEXT_LEN 防护)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )  # shell 盖墙钟;排序 + 0039 未读/已读比较与保留清理键


class DMReadCursor(SQLModel, table=True):
    # 已读游标(状态写,按 (reader_uid, peer_uid) 复合主键覆盖;对齐 dm_records.DMReadCursorWrite)。
    # 一表两用(messaging.md):未读 = DMMessage.created_at > 本表 read_through_ts;发件人已读回执 = 查 peer_uid=自己 的行。
    reader_uid: int = Field(foreign_key="user.id", primary_key=True)  # 读者(收件人)不可变账号主键(FK → user.id)
    peer_uid: int = Field(foreign_key="user.id", primary_key=True)  # 对端(发件人)不可变账号主键(FK → user.id)
    read_through_ts: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )  # 读到此刻为止(含);后写覆盖前写(状态写,只留最新进度)
