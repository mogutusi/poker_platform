# delayDB 写通道

## 一句话定位

**delayDB 是「内存权威 → DB」的滞后落库写通道。** `reduce` 改的是内存(经工作副本 commit),改动以 `Persist` 事件交给本通道,由 **PersistWriter**(全进程唯一 DB 写者)**周期批量**落库,异步追平 DB。core 不碰 DB,只产出 `Persist`;一切落库都在 shell。

> 本文只讲「`Persist` 出来之后怎么落库」。整体存储模型(内存权威、载入一次、工作副本回滚)见 **[storage.md](storage.md)**;**表结构(`app/db/` 模型)与 Alembic 迁移用法**见 **[db-migrations.md](db-migrations.md)**;前置概念在那里。
> 当前实例:**全局积分**(状态写)、**手牌记录**(事件写)、**私信**(事件写 `DMWrite` 已落地 [0038](refactor/changes/0038-dm-send-deliver.md) + 状态写 `DMReadCursorWrite` 已落地 [0039](refactor/changes/0039-dm-read-cursor.md),见 [messaging.md](messaging.md))。新实体按下文「两类写」归类即可接入,不新增通道。

## 两类写(接入新实体先归类)

| 类别 | 语义 | 例子 | 落库 | 能否覆盖 | 键 |
|---|---|---|---|---|---|
| **状态写**(state-write) | 实体「现在的样子」 | 全局积分 `points` / 已读游标 `DMReadCursorWrite` | 定向 UPDATE 或 UPSERT(见下「行是否预存」) | **可覆盖**(同键只留最新) | `(table, pk)` |
| **事件写**(append-write) | 「发生过一件事」 | 手牌记录 + 参与者 / 私信 `DMWrite` | INSERT | **不可覆盖**(每条都落) | 业务唯一键(幂等) |

判据:**描述「实体当前状态」(可覆盖)还是「一次已发生的事实」(必追加)?** 拿不准默认归事件写——覆盖一个本该追加的实体会**静默丢数据**,代价远高于多落几条。

> **状态写「行是否预存」两子情形(0039 落定)**:① **行必预存**(`PointsWrite`:User 行 seed/载入一次必在)→ **定向 UPDATE**(只盖携带列、保住其它列);② **行非必存**(`DMReadCursorWrite`:首次读某会话时 `(reader,peer)` 行尚无)→ **UPSERT**(唯一写者下 SELECT-by-PK → 无则 INSERT、有则 UPDATE,race-free、跨方言,同事件写幂等思路)。两者都满足「同键覆盖只留最新」的状态写语义,差别只在落库时行在不在。

**为什么状态写要覆盖而非 FIFO**:内存是权威,DB 只需追平到当前值。用户一秒内买入两次再退分,DB 只关心最终值;中间值落库是纯浪费。同键后写直接盖前写,N 次变更合成 1 次落库。

## 数据结构:写缓冲 `WriteBuffer`(0024 落地,精确签名见 [persist.py](../app/shell/persist.py))

双缓冲两个桶 + 单入口 `put`:

- `_dirty: dict[StateKey, payload]` —— 状态写,同键覆盖(`StateKey = ("user", str(uid))` 等)。
- `_appends: list[payload]` —— 事件写,逐条追加。
- `put(payload)`:**单入口**,内部 `_state_key(payload)` 归类(状态写→键入 `_dirty`、事件写或未知→ append 进 `_appends`)。GameLoop.dispatch 仍调 `self.persist.put(p)`,**同步内存写、不 `await`**(守不变量 3)。
- `swap() -> (dirty, appends)`:同步取走两桶并置空(双缓冲;PersistWriter 用)。
- `requeue(dirty, appends)`:失败回灌(状态写 `setdefault` 更新者优先、事件写前插)。
- `is_empty()` / `snapshot()`(只读调试)/ `__len__`。

> **0024 偏离(已对齐代码)**:不用 `StateWrite`/`AppendWrite` 包装类、不暴露 `put_state`/`put_append` 双方法——payload 自带识别字段(`PointsWrite.uid` / `HandRecordWrite.dedupe_key`),由 `_state_key` 一处归类即可,dispatch 调用点 `put(payload)` 不动。`swap` 返回的 `dirty`(dict)/`appends`(list)分桶本身即承载「状态写 vs 事件写」语义,无需包装类。

## 关键并发不变量:先 swap 后 await

缓冲被两个协程碰——GameLoop 的 `dispatch`(写)和 PersistWriter(读+落库)。单线程 asyncio 下安全的唯一前提:

> **`put_*` 与 `swap` 都是同步无 `await` 的纯内存操作;PersistWriter 必须「先 `swap()` 同步拿走并清空,再 `await` 落库」。**

这是**双缓冲**:`swap` 之后,正在落库的批次是 PersistWriter 私有局部变量;`await commit()` 期间 GameLoop 新写进的是**新的空缓冲**,既不丢也不混。

**反例(必错)**:PersistWriter 边遍历 `self._dirty` 边 `await` 写库——`await` 让出时 GameLoop 改同一个 dict → `RuntimeError: dict changed size` 或丢写。**务必先 swap 后 await,绝不持缓冲本体跨 `await`。**

## PersistWriter 主循环(0025 落地;精确实现见 [persist.py](../app/shell/persist.py))

`run()` 是「`sleep` → `flush_once()`」的薄壳;落库后端抽象在 **`Persister` 协议**(`async flush(dirty, appends)`)之后,真实现(`to_orm` + session,P4 三 `OrmPersister`)与控制流解耦:

```python
async def flush_once(self) -> bool:          # 抽出供直测(同 timer.tick)
    if self._buf.is_empty(): return False
    dirty, appends = self._buf.swap()        # 先 swap 同步取走清空(双缓冲),再 await
    try:
        await self._persister.flush(dirty, appends)   # OrmPersister:一批一短事务 UPDATE(状态写)/INSERT(事件写)+ commit
    except asyncio.CancelledError:           # 关闭取消落在 flush 半途 → 先回灌再 re-raise,由 drain 补落(不丢)
        self._buf.requeue(dirty, appends); raise
    except Exception:                        # 失败:未达毒丸则整批回灌、达阈值则丢批
        self._fail_streak += 1
        if self._fail_streak >= self._max_retry:   # 毒丸:CRITICAL + 丢批 + 复位计数
            log.critical(...); self._fail_streak = 0
        else:
            self._buf.requeue(dirty, appends)       # 更新者优先,下周期重试
        return True
    self._fail_streak = 0; return True       # 成功复位失败计数
```

> **0025 偏离(已对齐代码)**:db.md 早先伪码把 `to_orm` + `session.merge/add/commit` 内联在 `run()`;0025 把落库后端抽成 `Persister` 协议(`flush_once` 只调 `persister.flush`),使 PersistWriter 控制流脱真 DB、纯 fake 可测;`to_orm`+session 成为 P4 三 `OrmPersister.flush` 的实现体(dev 用 `NullPersister` 丢弃)。并加 `CancelledError` 回灌守关闭半途丢批 + drain 节流(见下「优雅关闭」)。

> **0028 落地(`OrmPersister` 写路径,已对齐代码)**:`to_orm` + `OrmPersister` + async engine/session 落在 **[app/db/](../app/db/)**(`orm_persister.py` / `engine.py`),**不在** `shell/persist.py`——保持 `persist.py` 纯 asyncio、SQLAlchemy-free(0025 抽 `Persister` 协议的初衷);`OrmPersister` 靠结构化协议满足 `Persister`,只 import `core.records`+`db.models`+sqlalchemy、**不 import shell**(见 [changes/0028](refactor/changes/0028-p4-orm-persister.md))。落库语义两处细化(对齐下文「失败与重试 / 事务分组」):**状态写=定向列 UPDATE**(`User` 有 `nickname` 等 `PointsWrite` 不拥有的列,整行 `merge` 会写 NULL ⇒ 只 `UPDATE ... SET points WHERE id=uid`;内存权威+载入一次保证行已存在);**事件写幂等=单写者下 `SELECT by dedupe_key` 再 INSERT**(唯一写者无并发竞争 ⇒ race-free 且跨方言,免 `ON CONFLICT` 的 sqlite/pg 二分;unique 索引兜底)。**dev/测试 async driver=`aiosqlite`**(`make_engine` 给 sqlite 装 `PRAGMA foreign_keys=ON` 使其与 postgres 一致强制 FK);postgres 走现有 `psycopg`。**`OrmPersister` 接进 lifespan 替 `NullPersister` + 种子/载入 dev 用户 = 0029**。

**为什么周期而非「来一条写一条」**:给覆盖一个窗口。立即消费则窗口为零、覆盖退化成 FIFO。`DB_FLUSH_INTERVAL_MS` 是「同实体多次变更合并的时间窗」,也是「积分落库最多滞后多久 / 崩溃窗口」——积分非货币,可放宽。可选增强:条目超 `DB_FLUSH_MAX_BATCH` 提前 flush(0025 未做)。

## 失败与重试

```python
# WriteBuffer.requeue(0024):状态写 setdefault(更新者优先)、事件写前插
def requeue(self, dirty, appends) -> None:
    for key, payload in dirty.items():
        self._dirty.setdefault(key, payload)   # 更新者优先:期间已有更新的写就保留新的,绝不旧盖新
    self._appends[:0] = appends                # 事件写放回缓冲头,下批重新 INSERT
```

- **状态写回灌用 `setdefault`(更新者优先)**:这是覆盖语义下唯一的正确性要点——回灌的是上一批的旧值,若期间 GameLoop 又写了更新值,**必须保留更新的**,否则旧值盖新值 = 把内存权威最新状态写错。
- **覆盖红利**:重试不必记「重试到第几条 / 累计多少增量」,状态写永远只关心当前值,回灌后下周期再 UPSERT,天然幂等。
- **事件写幂等**:每批一个事务,失败整批回滚、什么都没落,原样放回重 INSERT 不会重复。`dedupe_key` 是额外保险(防"commit 成功但进程在记账前崩")——**全进程唯一写者**下落库用 `SELECT by dedupe_key` 在不在、不在才 INSERT(race-free、跨方言;`dedupe_key` unique 索引兜底真撞 → IntegrityError → 整批回滚 + 下批 SELECT 见即跳)。`INSERT ... ON CONFLICT (dedupe_key) DO NOTHING` 是要 DB 层强制时的等价替代(0028 落地用 SELECT-then-INSERT)。
- **毒丸(永久失败)**:同一批连续失败**达** `DB_WRITE_MAX_RETRY` 次(`fail_streak >= 阈值`)→ 落 CRITICAL、丢批(别卡死后续),留人工介入。这是 bug 信号。

## 事务分组 & session

- **手牌记录与其参与者必须同事务**(参与者外键引用 record.id):一个事件写单元 = 「一条 record + 它全部 participants」,整体 INSERT、整体成败。
- 状态写各实体独立,可与事件写同批同事务;一批失败整批回滚,回灌安全。
- **每批一个短事务,用完即关 session**;PersistWriter 持自己的 `AsyncSessionLocal`,**不复用** WS 请求注入的 `DBsession`。
- 唯一写者 ⇒ **全程无 `with_for_update` / 无行锁**。读路径(REST 查手牌、查余额)走各自请求级 `DBsession`,与写路径互不干扰(读 DB 可能比内存旧,实时判定一律以内存为准)。

## 优雅关闭(drain · 必须有)

「内存权威 + 滞后落库」意味着任一时刻缓冲里都可能压着**已对玩家生效、未落库**的变更。进程优雅退出前必须 flush 干净,否则凭空丢数据。关闭顺序(FastAPI lifespan 编排):

1. **停 Receiver**:不再接新连接 / 新命令。
2. **排空 inbox + 停 GameLoop**:在途命令处理完,不再产生新 `Persist`。
3. **PersistWriter 终结 flush**:循环 `swap` + 落库直到 `is_empty()`。
4. 关 DB 连接池、关 Sender。

drain 落库仍可能失败:有限重试(`DB_DRAIN_TIMEOUT_MS` 上限)后放弃并 **CRITICAL** 落日志——进程要退,这一小段窗口接受。

## 崩溃语义

- **非优雅崩溃**:缓冲里未 flush 的状态写 / 事件写全丢。状态写丢「最近未落库的最新值」,事件写丢「最近几手记录」。因积分非货币、手牌量小,**接受**;重启从 DB 载入初值,无需对账。
- `DB_FLUSH_INTERVAL_MS` 越小崩溃窗口越窄但落库越频——它是**崩溃窗口**旋钮,不是性能旋钮。

## `Persist` 接口(core ↔ 本模块的契约)

> **下方 `class X(BaseModel)` 是示意**:精确签名以代码为准——所有 Write 载荷实为 **frozen dataclass**(`PointsWrite`/`HandRecordWrite` 在 [core/records.py](../app/core/records.py),`DMWrite` 在 [app/db/dm_records.py](../app/db/dm_records.py),共享基类 `PersistPayload`)。**core 产 vs shell 产**:`PointsWrite`/`HandRecordWrite` 由 reduce 产、置 core;`DMWrite`/`DMReadCursorWrite` 由 **shell 私信路由产、core 永不碰**,故置 db 层(`dm_records.py`),不入 core/records.py。

```python
# 状态写(覆盖)
class PointsWrite(BaseModel):
    uid: int              # StateKey 的 pk = 不可变 User.id(= UserState.uid),绝不用可变的 nickname
    points: int           # 内存权威的最新全量值,不是增量

# 事件写(追加)
class HandRecordWrite(BaseModel):
    dedupe_key: str       # = f"{room}:{hand.seq}",由 core 生成(见 core.md「手牌标识」)
    start_time: datetime  # core 携带(开局时 shell 经 StartHand 带入的墙钟值)
    end_time: datetime | None  # core 产出时为 None(不读时钟)→ shell 在 dispatch 派发本 Persist 时盖墙钟(落库前必非 None)
    final_pot: int
    participants: list[ParticipantWrite]   # 每个含 uid / initial_points / final_points(uid = 不可变 User.id)

# 事件写(追加)· 私信一条(见 messaging.md「私信:未读收件箱」;已落地 0038,app/db/dm_records.py)
class DMWrite(BaseModel):
    dedupe_key: str       # = msg_id,shell 生成(uuid4().hex;比 f"{from_uid}:{微秒}" 稳,免同微秒撞键);幂等 INSERT
    from_uid: int         # 发件人不可变 User.id(绝不用可变 nickname)
    to_uid: int           # 收件人不可变 User.id
    text: str             # 私信正文;不得带 hole_cards/deck(log.md 红线)
    created_at: datetime  # shell 盖墙钟;既是展示时间,也是「未读/已读」比较与保留清理的排序键

# 状态写(覆盖)· 已读游标:某收件人读某对端读到了几时(已落地 0039,app/db/dm_records.py;行非必存 ⇒ UPSERT)
class DMReadCursorWrite(BaseModel):
    reader_uid: int          # 读者(收件人)User.id —— StateKey 之一
    peer_uid: int            # 对端(发件人)User.id —— key=("dm_cursor", reader_uid, peer_uid)
    read_through_ts: datetime # 读到此刻为止(含);未读 = 该对话 created_at > read_through_ts;后写覆盖前写(只留最新游标)
```

> **墙钟由 shell 盖**:`end_time` 是手牌结束的真实时刻,但 core 不读时钟——它产出 `HandRecordWrite` 时 `end_time` 为空,由 dispatch(shell)盖上 `now()` 再进写缓冲。`start_time` 则是 core 从 `StartHand` 命令携带过来的值,同属外移(见 [core.md](core.md))。

- payload 必须是**快照值**:core 产出 `Persist` 那一刻就是不可变值(int / 新构造的记录),不带 `world` 活引用(配合工作副本回滚,见不变量 7)。
- **新增持久化实体** = 在状态写/事件写里选语义 + 给键,不另起炉灶、不加第三种通道(受「不过度解耦」约束)。
- **私信两类写由 shell 直接 `put`,不经 core/`Persist`**:`PointsWrite`/`HandRecordWrite` 是 core 产 `Persist`、GameLoop.dispatch 代投;`DMWrite`/`DMReadCursorWrite` 则由 **shell 私信路由**直接 `put`(写缓冲的第二个生产者,`_state_key` 把 `DMReadCursorWrite` 归状态写、`DMWrite` 归事件写,见 [messaging.md](messaging.md))。两者都是快照值、都只经 PersistWriter 落库,故同列本接口。

## 配置(照 [config.md](config.md),不硬编码)

```python
class GameConfig(BaseSettings):
    DB_FLUSH_INTERVAL_MS: int = Field(ge=100, le=10000)   # 覆盖窗口 / 落库滞后 = 崩溃窗口
    DB_FLUSH_MAX_BATCH: int   = Field(ge=1, le=10000)     # 超过提前 flush(可选)
    DB_WRITE_MAX_RETRY: int   = Field(ge=1, le=100)       # 毒丸阈值
    DB_DRAIN_TIMEOUT_MS: int  = Field(ge=500, le=60000)   # 关闭 drain 上限
    DM_READ_RETENTION_SECONDS: int   = Field(ge=0, le=31536000)  # 已读私信保留多久后清(0=读后即删);未读不受限、一直保活
    DM_CLEANUP_INTERVAL_SECONDS: int = Field(ge=10, le=86400)    # PersistWriter 跑私信保留清理的周期
```

```ini
# poker.env
DB_FLUSH_INTERVAL_MS=500
DB_FLUSH_MAX_BATCH=500
DB_WRITE_MAX_RETRY=10
DB_DRAIN_TIMEOUT_MS=5000
DM_READ_RETENTION_SECONDS=604800   # 已读私信留 7 天后清
DM_CLEANUP_INTERVAL_SECONDS=3600   # 每小时跑一趟清理
```

## 与架构契约(必须守住)

1. **core 不碰 DB**,只产出 `Persist`(快照值);载入/落库全在 shell。core 内禁止 `import sqlalchemy` / `await commit`。
2. **`put_*` 同步无 `await`;PersistWriter 先 `swap` 后 `await`**(双缓冲),绝不持缓冲本体跨 `await`。
3. **状态写按键覆盖、只落最新;事件写逐条追加、靠唯一键幂等。** 别把事件写设成可覆盖。
4. **失败回灌「更新者优先」**(`setdefault`),绝不旧值盖新值。
5. **唯一写者 ⇒ 无行锁;优雅关闭前必须 drain。**
6. 载入(读 DB 进内存一次、绝不重载)属存储模型,见 [storage.md](storage.md)。

## 注意点

- **覆盖 ≠ 丢一致性**:落的是内存权威**当前值**,DB 追平即正确;被覆盖的中间值本就无需持久化。把这点和「事件写绝不可覆盖」分清,是本模块唯一易错处。
- **脱敏红线**:落库 payload **不得带 `hole_cards` / `deck`**(见 [log.md](log.md));手牌记录存**结果**(`initial_points`/`final_points`/`final_pot`),不是底牌。
- **读写分离**:实时判定一律读内存;DB 只服务事后查询与崩溃后冷启动初值。
- **私信是写缓冲的第二个生产者**:shell 私信路由 `put`(同步无 await)进缓冲(同 GameLoop.dispatch),唯一**写库者**仍是 PersistWriter(见 [messaging.md](messaging.md));**私信保留清理**(删已读满期的行)也归 PersistWriter——DELETE 是 DB 写,不另起写者(守唯一写者),周期 `DM_CLEANUP_INTERVAL_SECONDS`、保留期 `DM_READ_RETENTION_SECONDS`。
- **日志分级**:flush 成功 DEBUG、失败回灌 ERROR、毒丸 CRITICAL(数据丢失 + bug 信号)、drain 超时 CRITICAL。
