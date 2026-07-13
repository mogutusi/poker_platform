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
    # 国密鉴权列(P5,changes/0056;双钥轮换扩列 changes/0066)。均可空:既有行记 NULL = 未启用登录
    # (name 唯一 → 不能给常量 server_default 回填,故走可空)。这些列纯 DB/shell 鉴权字段,不进
    # world/UserState(auth.md/user.md 红线);脱敏红线:hash_password/k_cur/k_prev 不进日志。
    name: Optional[str] = Field(default=None, max_length=15, unique=True, index=True)  # 登录账号(不可变,唯一;登录按它查 + 选 K_user)
    hash_password: Optional[str] = Field(default=None, max_length=128)  # 密码哈希 "salt$rounds$digest"(0053 格式;salt/轮数已内嵌,无需另列)
    # K_user 双钥(auth.md §K_user 每周轮换):当前钥 + 上一把(宽限期内仍可登录);登录先试 k_cur 再试 k_prev。
    # *_until 一律 epoch 秒(float)——auth 全链时基是 float(SessionStore/now()/blob.ts),免 DateTime 在 sqlite 丢 tz 之坑。
    k_cur: Optional[str] = Field(default=None, max_length=64)  # 当前 SM4 密钥(hex,16B=32hex;解登录 blob 用;0056 的 k_user 重命名而来)
    k_cur_ver: Optional[int] = Field(default=None)  # 当前钥版本号(首发=1,轮换 +1);管理员记账用,不进登录协议(changes/0066 决策 1)
    k_cur_until: Optional[float] = Field(default=None)  # 当前钥「到期应轮换」时刻(epoch 秒);轮换任务挑 <=now 的轮换;NULL=不排程(dev 种子行);登录不查它(免 cron 迟跑锁死全员,changes/0066 决策 2)
    k_prev: Optional[str] = Field(default=None, max_length=64)  # 上一把 SM4 密钥(hex);宽限期内仍可登录(附 rotate 提示),给「还没换新钥的人」缓冲
    k_prev_ver: Optional[int] = Field(default=None)  # 上一把的版本号(= 轮换前的 k_cur_ver)
    k_prev_until: Optional[float] = Field(default=None)  # 旧钥宽限截止(epoch 秒);登录查它:过期即拒(这是真正的安全边界)


class HandRecord(SQLModel, table=True):
    # 一手牌结果记录(事件写,追加;对齐 core.records.HandRecordWrite)。
    id: Optional[int] = Field(default=None, primary_key=True)  # 自增主键
    dedupe_key: str = Field(max_length=128, unique=True, index=True)  # = f"{room}:{seq}";幂等 INSERT 唯一键
    room: str = Field(max_length=128, index=True)  # 房名(denormalized;供 GET /hands?room= 健壮过滤,免 dedupe_key LIKE,见 changes/0052)
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
