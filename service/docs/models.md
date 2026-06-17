# 三套表示与转换(models)

## 定位

同一份信息有**三套表示**,各司其职、**物理分开**;本文钉死它们的边界和**谁在哪里转换**,消除"域模型该不该直接上 wire / 落库"的歧义。

| 表示 | 是什么 | 谁用 | 定义在 |
|---|---|---|---|
| **域模型 domain** | core 权威内存状态:`World`/`Room`/`Hand`/`Player`/`Seat`/`UserState` | core/reduce 读写 | [core.md](core.md),纯 dataclass |
| **wire DTO** | 对外报文 `ClientMessage`/`ServerMessage` | 前后端 | .py Pydantic,治理见 [wire.md](wire.md) |
| **DB 模型** | 持久化行 `User`/`HandRecord`… | PersistWriter 落库 / REST 读 | SQLModel,见 [db.md](db.md) |

## 三个转换缝(谁在哪转)

```
            reduce 投影                         PersistWriter.to_orm
 domain ───────────────▶ wire DTO        Persist payload ──────────────▶ DB 模型
   │     (Broadcast/Personal 的 msg)            ▲                          
   └───────────────▶ Persist payload ──────────┘                          
            reduce 构造(PointsWrite/HandRecordWrite)        REST 读 DB → 响应 DTO
```

1. **域 → wire DTO**:在 **reduce**,构造 `Broadcast`/`Personal` 的 `msg`(`ServerMessage`)时把域状态**投影**成 DTO——**快照值**、按"谁能看"裁剪(他人底牌不给)。
2. **域 → Persist payload**:在 **reduce**,构造 `Persist(PointsWrite/HandRecordWrite)`(Pydantic 快照,见 [db.md](db.md))。
3. **Persist payload → DB 模型**:在 **PersistWriter**(`to_orm`),shell 把 payload 映射成 SQLModel UPSERT/INSERT。
4. **DB 模型 → 响应 DTO**:REST 读 DB、投影成响应 Pydantic(见 [rest.md](rest.md))。

## core 能 import 谁(关键澄清)

- **core 可 import wire 的 Pydantic 模型**:reduce 直接构造 `ServerMessage` 当事件 payload。Pydantic **不是**被禁的 `fastapi`/`sqlalchemy`/`websocket`(见 [core.md](core.md) 不变量 1);构造 DTO 对象 ≠ 序列化(序列化/加密在 Sender)。
- **core 不可 import** SQLAlchemy/SQLModel/FastAPI:DB 模型只活在 shell;域 ↔ DB 永不直接转,必经 `Persist` 这座桥(见 [storage.md](storage.md))。
- **决策(可改)**:让 reduce 产 wire DTO(现选,贴合"`Broadcast.msg` 即 ServerMessage")vs 让 reduce 产纯语义事件、由 shell 再投影成 DTO。后者 core 更纯但要在 shell 复制游戏语义,本规模选前者。

## 为什么不共用一套

- 域模型有内部字段(`deck`/`epoch`/`contributed`/`in_game_points`)**不该上 wire**(隐私/无关),也不一定落库。
- wire 按"谁能看"裁剪(他人底牌不给);DB **存结果不存底牌**(见 [core.md](core.md) 不变量 3 / [db.md](db.md))。
- 三者**独立演进**:改 wire 不动域;改 DB schema 走 Alembic 不动 wire;域模型重构不影响已落库数据。

## 与架构契约(必须守住)

1. **三套物理分开**;reduce 产出的 event/persist 带**投影后的快照 DTO**,不塞域对象活引用(不变量 7)。
2. **core 可 import wire Pydantic DTO,不可 import SQLAlchemy/SQLModel/FastAPI**;域 ↔ DB 必经 `Persist`。
3. **域→wire 投影只在 reduce;Persist→DB 映射只在 PersistWriter;DB→响应 在 REST**——转换缝固定,不散落。
4. **裁剪在投影时做**:他人底牌不进 `Broadcast`,底牌不落库——隐私在每个转换缝把关。

## 待定

- 具体 DTO / DB 字段在 .py(见 [wire.md](wire.md) / [db.md](db.md)),随实现定。
- 现有 [handrecord](../app/handrecord/) 的 SQLModel 要对齐 `HandRecordWrite`(见 [rest.md](rest.md))。
- 域模型与 wire DTO 各自的 .py 模块划分(命名/目录)随实现定。
