# 0002 · P0 基线:core 数据类型 + 工作副本 API

日期:2026-06-17 · 范围:`service/app/core/`、`service/app/shell/world.py`、`service/tests/`、`pyproject.toml`(dev 依赖)

## 背景 / 打算改什么

按 [TODO.md](../TODO.md) P0,落地 core 的数据类型与工作副本读写 API。这是后续所有阶段的地基,**本阶段不含游戏规则**(规则是 P1)。

环境:本机刚装 Poetry,先 `poetry install` 建 in-project `.venv`,并 `poetry add --group dev pytest pytest-asyncio`(见 [testing.md](../testing.md))。

打算新建(目标布局见 [README.md](../README.md) §3):

- `core/enums.py`:四套状态机 + `USER_STATUS_TRANSITIONS` 合法转移表(从 [pokertable/enums.py](../../app/pokertable/enums.py) 迁移)。
- `core/cards.py`:`Card`(suit/rank)纯数据 + treys 串转换(`Deck`/`Evaluator` 是 P1 的 `deck.py`)。
- `core/domain.py`:`World/Room/Hand/Player/Seat/UserState/EntryVote` dataclass。含 0001 钉死的新字段:`UserState.uid`、`Hand.epoch/seq/start_time/last_raise_size`、`Seat.in_game_points/new_here/wait_for_big_blind`、`Room.entry_vote/waive_entry_for`、`Player.has_acted`。
- `core/commands.py`:Command 全集,统一 `origin: str | None`,**不带 room**(`JoinRoom(room, uid, loaded)` 例外)。
- `core/events.py`:`Broadcast/Personal/Persist/TurnChanged/ClearAction`。
- `core/errors.py`:`ErrorCode` 枚举 + `Err(code, detail)`。
- `shell/world.py`:`World.checkout(cmd)`(按命令类型解析目标房,表见 [storage.md](../storage.md))+ `commit(work)`(房间增/删/替换 + users 表替换)。
- `tests/core/test_domain.py`、`tests/shell/test_world.py`:数据类型可构造 + checkout/commit 隔离与回滚。

## 设计决策(开工前定的)

- **core 用 `@dataclass`(可变)**,不用 Pydantic:reduce 原地改工作副本,dataclass 更轻、`copy.deepcopy` 直接可用;wire 的 Pydantic DTO 是另一套(P6,见 [models.md](../models.md))。
- **core 不 import `gameconfig`**:域 dataclass 不烤死配置默认值(`small_blind`/`buy_in`/座位数由 shell/lobby 构造时传入)。守不变量 1 的精神:core 不依赖基础设施。
- **`World.checkout/commit` 放 `shell/world.py`**:`checkout` 要读 `world.users` 解析房间(GameLoop 是唯一写者,允许),这是 shell 职责;core 的 reduce 只收 `work`。`Work` 工作副本容器也定义在这里。
- 旧 `app/pokertable/` 暂留作参考(README §2),P0 不删。

## 实际改了什么

新增文件(全部按计划落地):

- `app/core/enums.py`:`RoomStatus`(去掉冗余 `HAND_ENDED`)、`HandStatus`(去掉 `READY_TO_START`,保留 `next_status` 链)、`PlayerActionType`、`PlayerStatus`、`UserStatus` + `USER_STATUS_TRANSITIONS` / `USER_STATUS_SELF_TRANSITIONS`。补了 `(SITTING_IN, OFFLINE)`、`(OFFLINE, SITTING_IN)` 两条断线/重连转移(旧表缺,在玩之外的就座者断线会无法标 OFFLINE)。
- `app/core/cards.py`:`Card`(frozen+slots,可作 dict 键 / set 成员)、`CardSuit`/`CardRank`、`to_treys()`。从旧 `models.py` 的 Pydantic `Card` 改成纯 dataclass。
- `app/core/domain.py`:`UserState`(含 `uid`)、`Player`(含 `has_acted`)、`Hand`(含 `epoch`/`seq`/`start_time`/`last_raise_size`)、`Seat`(含 `in_game_points`/`new_here`/`wait_for_big_blind`)、`EntryVote`、`Room`(含 `entry_vote`/`waive_entry_for`/`leaving`)、`World`。
- `app/core/commands.py`:`Command` 基类(`origin: str | None`)+ 全集;系统命令带 `nick` 字段。
- `app/core/events.py`:`Broadcast`/`Personal`/`TurnChanged`/`ClearAction`/`Persist` + `ServerMessage`/`PersistPayload` 占位基类(P4/P6 落地)。
- `app/core/errors.py`:`ErrorCode` 枚举 + `Err(code, detail)`。
- `app/shell/world.py`:`Work` 容器 + `checkout(world, cmd)`(`_target_room` 实现 storage.md 解析表)+ `commit(world, work)`(新建/替换/销毁 + users 表替换)。
- `tests/builders.py`、`tests/core/test_domain.py`(14)、`tests/shell/test_world.py`(9);`pyproject.toml` 加 `[tool.pytest.ini_options]`(pythonpath/testpaths/asyncio_mode)。
- 环境:`poetry install` 建 `.venv`;`poetry add --group dev pytest pytest-asyncio`。

**23 个测试全绿**;脚本校验 core 无 fastapi/sqlalchemy/sqlmodel/websocket/asyncio 导入。

### 偏离设计 / 决策

- **`checkout`/`commit` 写成模块级函数**(`world_api.checkout(world, cmd)`),而非 `World` 的方法。理由:`World` 是 core dataclass,把 shell 的 checkout 逻辑挂上去会让 core 类型耦合 shell 职责;模块函数更干净。文档伪码写 `world.checkout(...)`,语义一致,签名以实现为准(README §0 允许)。
- **新增 `Work.room_existed`**:`commit` 用不到(新建/替换同写回),但 P1 的 reduce 需要据它区分「JoinRoom 到不存在的房 → 新建」与「已存在 → 加入」,先在 P0 备好。
- **`Seat.wait_for_big_blind`**:rules.md ① 提到「等大盲免费」是个 wire 标志,落成 `Seat` 字段。
- **`Room.leaving`**:rules.md ④ 局中 `LeaveRoom` auto-fold + 标「离桌中」待手尾驱逐,落成 `Room` 字段。
- 旧 `app/pokertable/` 未删(README §2 视为参考)。

## 待办 / 下一步

- 进 P1:`core/deck.py` + `core/rules/` + `core/reduce.py` + `tests/core/` 穷举用例(对齐 [rules.md](../rules.md) 编号)。
