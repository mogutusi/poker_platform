# 三套表示与转换(models)

## 定位

同一份信息有三套表示,物理分开、各司其职。本文钉死两件事:边界在哪,以及谁在哪转换。

| 表示 | 是什么 | 谁用 | 定义在 |
|---|---|---|---|
| **域模型 domain** | core 权威内存状态:`World`/`Room`/`Hand`/`Player`/`Seat`/`UserState` | core/reduce 读写 | [core.md](core.md),纯 dataclass |
| **wire DTO** | 对外报文 `ClientMessage`/`ServerMessage` | 前后端 | Pydantic,治理见 [wire.md](wire.md) |
| **DB 模型** | 持久化行 `User`/`HandRecord`… | PersistWriter 落库 / REST 读 | SQLModel,见 [db.md](db.md) |

## 三个转换缝(谁在哪转)

每个缝都固定在一个地方,不散落。

```
            reduce 投影                         PersistWriter.to_orm
 domain ───────────────▶ wire DTO        Persist payload ──────────────▶ DB 模型
   │     (Broadcast/Personal 的 msg)            ▲
   └───────────────▶ Persist payload ──────────┘
            reduce 构造(PointsWrite/HandRecordWrite)        REST 读 DB → 响应 DTO
```

1. **域 → wire DTO**:在 reduce。构造 `Broadcast`/`Personal` 的 `msg`(`ServerMessage`)时,把域状态投影成 DTO;投影出的是快照值,并按「谁能看」裁剪——他人底牌不给。
2. **域 → Persist payload**:在 reduce。构造 `Persist(PointsWrite/HandRecordWrite)`,同样是快照(见 [db.md](db.md))。
3. **Persist payload → DB 模型**:在 PersistWriter 的 `to_orm`。shell 把 payload 映射成 SQLModel 的 UPSERT/INSERT。
4. **DB 模型 → 响应 DTO**:在 REST。读 DB、投影成响应 Pydantic(见 [rest.md](rest.md))。

## core 能 import 谁(关键澄清)

一句话:core 可以 import wire 的 Pydantic,不可以 import 任何 DB / 框架库。

**core 可 import wire 的 Pydantic 模型**

reduce 直接构造 `ServerMessage` 当事件 payload。Pydantic 不在被禁之列(禁的是 `fastapi`/`sqlalchemy`/`websocket`,见 [core.md](core.md) 不变量 1):构造 DTO 对象 ≠ 序列化,序列化 / 加密在 Sender。

**core 不可 import SQLAlchemy/SQLModel/FastAPI**

DB 模型只活在 shell。域 ↔ DB 永不直接转,必经 `Persist`(见 [storage.md](storage.md))。

**决策(可改)**

reduce 直接产 wire DTO,而不是产纯语义事件再由 shell 投影。后者 core 更纯,但要在 shell 复制一遍游戏语义,本规模不值。

## 为什么不共用一套

三条理由:

- **字段不通用**:域模型有内部字段(`deck`/`epoch`/`contributed`/`in_game_points`)不该上 wire(隐私 / 无关),也不一定落库。
- **可见性不同**:wire 按「谁能看」裁剪;DB 存结果不存底牌(见 [core.md](core.md) 不变量 3 / [db.md](db.md))。
- **演进独立**:改 wire 不动域;改 DB schema 走 Alembic、不动 wire;域模型重构不影响已落库数据。

## 与架构契约(必须守住)

1. **三套物理分开**;reduce 产出的 event/persist 带投影后的快照 DTO,不塞域对象活引用(不变量 7)。
2. **core 可 import wire Pydantic DTO,不可 import SQLAlchemy/SQLModel/FastAPI**;域 ↔ DB 必经 `Persist`。
3. **转换缝固定,不散落**:域→wire 只在 reduce;Persist→DB 只在 PersistWriter;DB→响应在 REST。
4. **裁剪在投影时做**:他人底牌不进 `Broadcast`,底牌不落库——隐私在每个转换缝把关。

## 待定

**出站载荷已落 `app/wire/`(0017)**

- reduce 直接构造 [app/wire/server.py](../app/wire/server.py) 的 Pydantic 可辨识联合 DTO,作 `Broadcast`/`Personal` 的 `msg`;[events.py](../app/core/events.py) 的 `Broadcast.msg`/`Personal.msg` 改引 `app.wire.server.ServerMessage`;临时的 `core/messages.py` 已删除。
- 隐私由结构保证:广播 DTO 根本没有 `hole_cards`/`deck` 字段。

**Persist 事件写载荷仍在 `core/records.py`**

- 内含 `HandRecordWrite`/`ParticipantWrite`/`PointsWrite`,挂 [events.py](../app/core/events.py) 的 `PersistPayload`;它们不上 wire,所以与出站消息分文件。
- 0011 新增前两者,0014 补 `PointsWrite`(见 [changes/0011](refactor/changes/0011-p1-player-action-showdown.md)/[0014](refactor/changes/0014-p1-inhand-lifecycle.md)/[0017](refactor/changes/0017-wire-first-batch.md))。

**DB 模型已落 [`app/db/models.py`](../app/db/models.py)(0026)**

对齐关系与迁移用法见 [db-migrations.md](db-migrations.md)。原型 handrecord 模块已于 0027 拆除。

**仍未定的部分**

- 具体 DTO / DB 字段以 .py 为准(见 [wire.md](wire.md) / [db.md](db.md))。
- 域模型与 wire DTO 的模块划分随实现定。
