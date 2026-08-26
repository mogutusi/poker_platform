# delayDB 写通道

## 一句话定位

delayDB 是「内存权威 → DB」的滞后落库写通道:内存改完先返回,DB 稍后追平。

链路分四步:

1. `reduce` 改内存:GameLoop 处理命令前深复制出工作副本,reduce 只改副本、成功才 commit 装回权威(见 [storage.md](storage.md))。
2. `reduce` 产出 `Persist` 事件。
3. GameLoop 把事件同步写进写缓冲。
4. PersistWriter 周期批量落库,它是全进程唯一的 DB 写者。

core 不碰 DB,落库全在 shell。

> 本文只讲「`Persist` 出来之后怎么落库」。存储模型见 [storage.md](storage.md);表结构与迁移见 [db-migrations.md](db-migrations.md)。

当前已接入的实例:全局积分(状态写)、手牌记录(事件写)、私信正文(事件写 `DMWrite`,[0038](refactor/changes/0038-dm-send-deliver.md))、私信已读游标(状态写 `DMReadCursorWrite`,[0039](refactor/changes/0039-dm-read-cursor.md))。私信两项见 [messaging.md](messaging.md)。新实体按「两类写」归类接入,不新增通道。

## 两类写(接入新实体先归类)

| 类别 | 语义 | 例子 | 落库 | 能否覆盖 | 键 |
|---|---|---|---|---|---|
| **状态写**(state-write) | 实体「现在的样子」 | 全局积分 `points` / 已读游标 `DMReadCursorWrite` | 定向 UPDATE 或 UPSERT | 可覆盖(同键只留最新);**例外**:已读游标只前进不后退(0098) | `(table, pk)` |
| **事件写**(append-write) | 「发生过一件事」 | 手牌记录 + 参与者 / 私信 `DMWrite` | INSERT | 不可覆盖(每条都落) | 业务唯一键(幂等) |

归类判据:这条数据描述「实体当前状态」,还是「一次已发生的事实」?拿不准就默认归事件写——覆盖本该追加的数据会静默丢失。

状态写为什么用覆盖而不是 FIFO 排队:内存是权威,DB 只需追平当前值,同键后写盖前写,N 次变更合成 1 次落库。

### 状态写的两种落法(0039 落定)

按「目标行是否一定已存在」分:

- **行必预存 → 定向 UPDATE**。例:`PointsWrite`,User 行经 seed / 载入必然已在。只盖 payload 携带的列;不能用整行 `merge`,那会把 payload 没有的列写成 NULL。
- **行非必存 → UPSERT**。例:`DMReadCursorWrite`,首次读某会话时 `(reader,peer)` 行还不存在。做法:唯一写者下先 SELECT-by-PK,无则 INSERT、有则 UPDATE,race-free 且跨 sqlite/pg 方言通用。

## 数据结构:写缓冲 `WriteBuffer`(0024 落地,精确签名见 [persist.py](../app/shell/persist.py))

两个桶 + 一个入口。双缓冲 = 写者取走整批后,新写进的是另一份空缓冲。

- `_dirty: dict[StateKey, payload]`:状态写,同键覆盖。`StateKey` 形如 `("user", str(uid))`。
- `_appends: list[payload]`:事件写,逐条追加。

方法:

- `put(payload)`:唯一入口,内部用 `_state_key(payload)` 归类——状态写键入 `_dirty`,事件写或未知类型 append 进 `_appends`。GameLoop.dispatch 调 `self.persist.put(p)`,同步内存写、不 `await`(守不变量 3)。
- `swap() -> (dirty, appends)`:同步取走两桶并置空,供 PersistWriter 用。
- `requeue(dirty, appends)`:失败回灌,状态写 `setdefault`(更新者优先)、事件写前插。
- `is_empty()` / `snapshot()`(只读调试)/ `__len__`。

> 与 0024 原案的偏离(已对齐代码):不用 `StateWrite`/`AppendWrite` 包装类,也不设 `put_state`/`put_append` 双方法。payload 自带识别字段(`PointsWrite.uid` / `HandRecordWrite.dedupe_key`),`_state_key` 一处归类即可。

## 关键并发不变量:先 swap 后 await

缓冲被两个协程碰:GameLoop 的 `dispatch`(写)、PersistWriter(读 + 落库)。单线程 asyncio 下安全的唯一前提是:

> **`put_*` 与 `swap` 都是同步无 `await` 的纯内存操作;PersistWriter 必须先 `swap()` 同步拿走并清空,再 `await` 落库。**

安全的道理:`swap` 之后正在落库的批次是 PersistWriter 私有的局部变量,`await commit()` 期间新写进的是新的空缓冲,不丢不混。

反例(必错):边遍历 `self._dirty` 边 `await` 写库,`await` 让出时 GameLoop 改同一个 dict,结果是 `RuntimeError: dict changed size` 或丢写。铁律:绝不持缓冲本体跨 `await`。

## PersistWriter 主循环(0025 落地;精确实现见 [persist.py](../app/shell/persist.py))

`run()` 是一层薄壳:可唤醒等待(`wait_for(_wake, interval)`,0073)→ `flush_once()`。落库后端抽象在 `Persister` 协议之后(`async flush(dirty, appends)`),与控制流解耦:dev 用 `NullPersister` 直接丢弃,纯 fake 也能测。

```python
async def flush_once(self) -> bool:          # 抽出供直测(同 timer.tick)
    if self._buf.is_empty(): return False
    dirty, appends = self._buf.swap()        # 先 swap 同步取走清空,再 await
    try:
        await self._persister.flush(dirty, appends)   # 一批一短事务 UPDATE/INSERT + commit
    except asyncio.CancelledError:           # 关闭取消落在半途 → 先回灌再 re-raise,由 drain 补落
        self._buf.requeue(dirty, appends); raise
    except Exception:
        self._fail_streak += 1
        if self._fail_streak >= self._max_retry:   # 毒丸:CRITICAL + 丢批 + 复位计数
            log.critical(...); self._fail_streak = 0
        else:
            self._buf.requeue(dirty, appends)       # 更新者优先,下周期重试
        return True
    self._fail_streak = 0; return True       # 成功复位失败计数
```

> 0028 落地(`OrmPersister` 写路径,见 [changes/0028](refactor/changes/0028-p4-orm-persister.md)):
> - 分层:`to_orm` + `OrmPersister` + async engine/session 在 [app/db/](../app/db/)(`orm_persister.py` / `engine.py`)。`shell/persist.py` 保持纯 asyncio、SQLAlchemy-free。`OrmPersister` 靠结构化协议满足 `Persister`,只 import `core.records` + `db.models` + sqlalchemy,不 import shell。
> - 状态写 = 定向列 UPDATE:`UPDATE ... SET points WHERE id=uid`;`User` 确有 `PointsWrite` 不拥有的列,故不用整行 `merge`。
> - 事件写幂等 = `SELECT by dedupe_key` 再 INSERT(unique 索引兜底);唯一写者下无并发竞争,不必按 sqlite/pg 分别写 `ON CONFLICT`。
> - async driver:dev / 测试用 `aiosqlite`,postgres 走 `psycopg`。`make_engine` 给 sqlite 装 `PRAGMA foreign_keys=ON`,与 postgres 一致强制 FK。
> - `OrmPersister` 接进 lifespan 替换 `NullPersister`,以及种子 / 载入 dev 用户 = 0029。

为什么周期落库,而不是「来一条写一条」:立即消费则覆盖窗口为零,状态写退化成 FIFO。

`DB_FLUSH_INTERVAL_MS` 一个旋钮身兼两职:同实体多次变更的合并窗口,以及落库滞后 = 崩溃窗口;积分非货币,可放宽。

可选增强:条目数超 `DB_FLUSH_MAX_BATCH` 时提前 flush(0025 未做)。

## 运行期落库屏障 `barrier()`(0073)

`await persistwriter.barrier(timeout_s=None) -> bool`:等「调用时刻已在缓冲、或已在飞的写」全部落库。这是「强制等落库」的运行期形态,关闭期的形态就是 drain。

目前的消费者是 `JoinRoom`:载入前先令 DB 追平,封住 0072·N1「驱逐后重读陈旧 DB」的窗口(见 [storage.md](storage.md)「载入屏障」)。

**返回值**

- True = 已落库,或本来就没有待写。
- False = 超时、毒丸丢批,或写者已停止。调用方拿到 False 必须 fail-closed(`JoinRoom` 回 `INTERNAL`),不能拿可能陈旧的 DB 值继续。

缺省超时与 drain 共用 `DB_DRAIN_TIMEOUT_MS`(决策·可改):两者同为「等落库上限」,不值得加第二个旋钮;正常路径只需等 ≤1 个 commit,超时只在 DB 异常时触发。

**机制**

barrier 登记一个等待者,并 set `_wake` 唤醒写者立即 flush。等待者在 `flush_once` 的 swap 之前被取走——因为它登记那一刻的待写必然落在本批或更早的批里。之后按落库结果分情况:

- 本批 commit 成功 → True;缓冲空、无可 flush → 即刻达成。
- 落库失败回灌 → 等待者一并放回,随重试继续等。
- 毒丸丢批 → False(数据已灭)。写者被 cancel → `run()` 的 finally 统一置 False,免得调用方悬死。

**在飞窗口**

快路径只在「缓冲空 **且** 无在飞批」时才直接返回 True:批已 swap 出、commit 还没落时,缓冲虽空但数据尚未持久。

**屏障只保证「落库」,不保证「已入缓冲」**

调用方若依赖某条命令产生的写,必须先 `inbox.join()` 确保该命令已被 GameLoop 处理(GameLoop 每条命令 `finally: task_done()`),再 `barrier()`。两步缺一不可,见 [changes/0073](refactor/changes/0073-persist-barrier-join-load.md)。

## 失败与重试

```python
# WriteBuffer.requeue(0024):状态写 setdefault(更新者优先)、事件写前插
def requeue(self, dirty, appends) -> None:
    for key, payload in dirty.items():
        self._dirty.setdefault(key, payload)   # 期间已有更新的写就保留新的,绝不旧盖新
    self._appends[:0] = appends                # 事件写放回缓冲头,下批重新 INSERT
```

**状态写回灌用 `setdefault`(更新者优先)**

回灌的是上一批的旧值,若这期间 GameLoop 又写了更新的值,必须保留新的,否则旧盖新、写错权威。这是覆盖语义下唯一的正确性要点;覆盖语义本身让重试天然幂等,不必记进度。

**事件写幂等**

每批一个事务,失败整批回滚、什么都没落,所以原样放回重 INSERT 不会重复。

`dedupe_key` 是额外保险,防的是「commit 成功但进程在记账前崩」:落库先 `SELECT by dedupe_key`,不在才 INSERT;unique 索引兜底——真撞上就 IntegrityError → 整批回滚 → 下批 SELECT 见到即跳过。`INSERT ... ON CONFLICT (dedupe_key) DO NOTHING` 是等价替代,0028 选的是 SELECT-then-INSERT。

**毒丸(永久失败)**

同一批连续失败达 `DB_WRITE_MAX_RETRY` 次 → 落 CRITICAL 日志 + 丢弃该批(不卡死后续批次),留给人工介入。触发即是 bug 信号。

## 事务分组 & session

- **手牌记录与其参与者必须同事务**:参与者外键引用 `record.id`,一个事件写单元 = 一条 record + 全部 participants,整体成败。
- 状态写各实体独立,可与事件写同批同事务;一批失败整批回滚,回灌安全。
- **每批一个短事务,用完即关 session**。PersistWriter 持自己的 `AsyncSessionLocal`,不复用 WS 请求注入的 `DBsession`;唯一写者 ⇒ 全程无 `with_for_update`、无行锁。
- 读路径(REST 查手牌、查余额)走各自的请求级 `DBsession`。读 DB 可能比内存旧,实时判定一律以内存为准。

## 优雅关闭(drain · 必须有)

缓冲里随时可能压着「已对玩家生效、但还没落库」的变更,进程优雅退出前必须 flush 干净。

关闭顺序(FastAPI lifespan 编排):

1. 停 Receiver:不再接新连接、新命令。
2. 排空 inbox + 停 GameLoop:在途命令处理完,不再产生新 `Persist`。
3. PersistWriter 终结 flush:循环 `swap` + 落库,直到 `is_empty()`。
4. 关 DB 连接池、关 Sender。

drain 落库仍可能失败:有限重试(上限 `DB_DRAIN_TIMEOUT_MS`)后放弃并落 CRITICAL。进程本来就要退,这一小段窗口接受。

## 崩溃语义

非优雅崩溃会丢缓冲里未 flush 的写:状态写丢最近的最新值,事件写丢最近几手记录。积分非货币、手牌量小,接受;重启时从 DB 载入初值,无需对账。

`DB_FLUSH_INTERVAL_MS` 越小崩溃窗口越窄、落库越频繁,它是崩溃窗口旋钮而非性能旋钮。

## `Persist` 接口(core ↔ 本模块的契约)

> 下方 `class X(BaseModel)` 是示意,精确签名以代码为准:所有 Write 载荷实为 frozen dataclass,共享基类 `PersistPayload`。
>
> - core 产的 `PointsWrite` / `HandRecordWrite`(由 reduce 产)在 [core/records.py](../app/core/records.py)。
> - shell 私信路由产的 `DMWrite` / `DMReadCursorWrite`(core 永不碰)在 [app/db/dm_records.py](../app/db/dm_records.py)。

```python
# 状态写(覆盖)
class PointsWrite(BaseModel):
    uid: int              # StateKey 的 pk = 不可变 User.id(= UserState.uid),绝不用可变的 nickname
    points: int           # 内存权威的最新全量值,不是增量

# 事件写(追加)
class HandRecordWrite(BaseModel):
    dedupe_key: str       # = f"{room}:{hand.seq}",core 生成(见 core.md「手牌标识」)
    start_time: datetime  # core 携带(开局时 shell 经 StartHand 带入的墙钟值)
    end_time: datetime | None  # core 产出时为 None(不读时钟)→ shell 在 dispatch 时盖墙钟(落库前必非 None)
    final_pot: int
    participants: list[ParticipantWrite]   # 每个含 uid / initial_points / final_points(uid = 不可变 User.id)

# 事件写(追加)· 私信(见 messaging.md「私信:未读收件箱」;0038 落地)
class DMWrite(BaseModel):
    dedupe_key: str       # = msg_id,shell 生成(uuid4().hex,免同微秒撞键);幂等 INSERT
    from_uid: int         # 发件人不可变 User.id
    to_uid: int           # 收件人不可变 User.id
    text: str             # 私信正文;不得带 hole_cards/deck(log.md 红线)
    created_at: datetime  # shell 盖墙钟;既是展示时间,也是「未读/已读」比较与保留清理的排序键

# 状态写(覆盖)· 已读游标(0039 落地;行非必存 ⇒ UPSERT)
class DMReadCursorWrite(BaseModel):
    reader_uid: int          # 读者(收件人)User.id —— StateKey 之一
    peer_uid: int            # 对端(发件人)User.id —— key=("dm_cursor", reader_uid, peer_uid)
    read_through_ts: datetime # 读到此刻为止(含);未读 = created_at > read_through_ts;**只前进不后退**(0098)
```

- **墙钟由 shell 盖**:core 不读时钟。`end_time` 产出时为 None,dispatch(shell)盖 `now()` 再进缓冲;`start_time` 是 core 从 `StartHand` 携带进来的值(见 [core.md](core.md))。
- **payload 必须是快照值**:产出那一刻就是不可变值(int,或新构造的记录),不带 `world` 活引用(不变量 7)。
- **新增持久化实体** = 在状态写 / 事件写里选一种语义 + 给一个键,不加第三种通道。
- **私信是写缓冲的第二个生产者**:两类私信写由 shell 私信路由直接 `put`,不经 core / `Persist`。`_state_key` 把 `DMReadCursorWrite` 归状态写、`DMWrite` 归事件写(见 [messaging.md](messaging.md))。它们同为快照值、同经 PersistWriter 落库,故同列本接口。

## 配置(照 [config.md](config.md),不硬编码)

```python
class GameConfig(BaseSettings):
    DB_FLUSH_INTERVAL_MS: int = Field(ge=100, le=10000)   # 覆盖窗口 / 落库滞后 = 崩溃窗口
    DB_FLUSH_MAX_BATCH: int   = Field(ge=1, le=10000)     # 超过提前 flush(可选)
    DB_WRITE_MAX_RETRY: int   = Field(ge=1, le=100)       # 毒丸阈值
    DB_DRAIN_TIMEOUT_MS: int  = Field(ge=500, le=60000)   # 关闭 drain 上限
    DM_READ_RETENTION_SECONDS: int   = Field(ge=0, le=31536000)  # 已读私信保留多久后清(0=读后即删);未读一直保活
    DM_CLEANUP_INTERVAL_SECONDS: int = Field(ge=10, le=86400)    # 私信保留清理周期
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

1. **core 不碰 DB**,只产出 `Persist`(快照值);载入 / 落库全在 shell。core 内禁止 `import sqlalchemy` / `await commit`。
2. **`put_*` 同步无 `await`;PersistWriter 先 `swap` 后 `await`**,绝不持缓冲本体跨 `await`。
3. **状态写按键覆盖、只落最新;事件写逐条追加、靠唯一键幂等。** 别把事件写设成可覆盖。
4. **失败回灌「更新者优先」**(`setdefault`),绝不旧值盖新值。
5. **唯一写者 ⇒ 无行锁;优雅关闭前必须 drain。** 例外:鉴权列同步直写是独立写路径(DB 权威、无内存副本);它与 PersistWriter 列不相交——鉴权路写 `SET hash_password`,PersistWriter 写 `SET points`——所以仍然无锁。见 [storage.md](storage.md)「鉴权列写路径」/ [changes/0064](refactor/changes/0064-p7-change-password.md)。
6. 载入(读 DB 进内存一次、绝不重载)属存储模型,见 [storage.md](storage.md)。

## 注意点

- **覆盖 ≠ 丢一致性**:落的是内存权威的当前值,DB 追平即正确。别和「事件写绝不可覆盖」混淆,这是本模块唯一易错处。
- **脱敏红线**:落库 payload 不得带 `hole_cards` / `deck`(见 [log.md](log.md))。手牌记录存的是结果,不是底牌。
- **读写分离**:实时判定一律读内存;DB 只服务事后查询与崩溃后的冷启动初值。
- **日志分级**:flush 成功 DEBUG,失败回灌 ERROR,毒丸 CRITICAL(数据丢失 + bug 信号),drain 超时 CRITICAL。

### 私信保留清理([0041](refactor/changes/0041-dm-retention-cleanup.md))

- 触发:`PersistWriter.maybe_cleanup`,附在 run 循环上,周期为 `DM_CLEANUP_INTERVAL_SECONDS`;调用 `Persister.cleanup_dms(cutoff=now-DM_READ_RETENTION_SECONDS)`,`OrmPersister` 执行 `DELETE dmmessage WHERE created_at<cutoff AND EXISTS(已读游标)`。
- DELETE 也是 DB 写,归唯一写者做,不另起协程。
- 未读永不删;已读但未过期的留着。
- 失败只落 ERROR + 跳过。操作幂等,下个周期重删即可。
