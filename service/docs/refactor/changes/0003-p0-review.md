# 0003 · P0 代码 review(对照设计文档)

日期:2026-06-17 · 范围:`service/app/core/`、`service/app/shell/world.py`(只改代码对齐文档,未改设计文档)

## 目的

按用户要求,对 P0(变更记录 0002)落地的代码逐文件对照设计文档做一次详细 review:找**冲突**、找**比文档更好的地方**、找**遗留问题**。本轮基准文档:[architecture.md](../architecture.md)、[core.md](../core.md)、[storage.md](../storage.md)、[user.md](../user.md)、[lobby.md](../lobby.md)、[error.md](../error.md)、[models.md](../models.md)、[connection.md](../connection.md)、[coding_principle.md](../coding_principle.md)。

> 本轮的漂移**几乎都在代码侧**(我第一版写偏了),文档是更对的一方 → 改代码对齐文档。与「代码发现比文档更优 → 改文档」是相反方向,特此区分。

## A. 冲突(代码偏离文档 → 已改代码对齐)

1. **`UserState.room` 类型** — 我写 `str | None = None`,文档 [user.md](../user.md) L26 / [lobby.md](../lobby.md) L33 是 `room: str`(必填)。
   - 依据:大厅用户**不进** `world.users`(lobby.md「大厅无 world 状态」),只活在 ConnectionManager;所以任何存在的 `UserState` 一定在某房间,`room` 永不为 None。
   - 改:`room: str`(去掉 Optional 和默认值)。`_target_room` 对纯大厅命令本就走 `world.users.get(nick) is None` 分支,不依赖 `UserState.room` 为 None,改后无影响。
   - **意义**:这也是「别盲目跟第一直觉」的实例——加 `| None` 看似稳妥,实则放松了「UserState ⇒ 在房」这条不变量,把不可能态变成可表达态。

2. **`ErrorCode` 取值大小写 + 命名** — 我用小写值(`"no_hand"`)且写了 `ROOM_NOT_FOUND`;[error.md](../error.md) L32-40 是**大写值**(`NO_HAND = "NO_HAND"`)且房间不存在叫 **`NO_SUCH_ROOM`**([lobby.md](../lobby.md) L35 一致)。
   - 依据:`code` 是 wire 契约(前端按 `code` 映射文案),必须与事实源 error.md 一致。
   - 改:全部取值改成与成员名一致的大写;`ROOM_NOT_FOUND` → `NO_SUCH_ROOM`。

3. **`Err.detail` 无默认值** — 我写 `detail: str`(必填);error.md L47 是 `detail: str = ""`,且文档示例 `Err(ErrorCode.NO_HAND)`(L81)不带 detail。
   - 我的版本会让那种调用报错。改:`detail: str = ""`。
   - 注:coding_principle「detail 要带上下文」是**写 reduce 时的纪律**,不该用「类型必填」去强制(会卡住合理的无 detail 用法)。

4. **`TurnChanged` 缺字段** — 我写 `TurnChanged(room, epoch)`;[connection.md](../connection.md) L103 的 dispatch 是 `TurnChanged(room, epoch, acting_nick, timeout_s)`,会 `timer.on_turn_changed(r, n, e, s)`。
   - 依据:Timer 不读 `world`,要靠事件知道**轮到谁**才能构造 `Timeout(nick, epoch)`。`acting_nick` 必须进事件。
   - 改:加 `acting_nick: str`。`timeout_s` 暂不加(见 D 遗留项)。

## B. 比文档更好 / 有意偏离(保留,已论证)

1. **`checkout`/`commit` 是 `shell/world.py` 模块函数,不是 `World` 方法** — [architecture.md](../architecture.md) L99/L108、[storage.md](../storage.md) 伪码写 `world.checkout()/world.commit()`。
   - 保留模块函数:`World` 是 **core dataclass**([models.md](../models.md)「域模型纯 dataclass」),若把 checkout(要 deepcopy、返回 shell 的 `Work`、读 `world.users` 解析房间)挂成它的方法,等于让 core 类型背上 shell 职责、并依赖 `Work` 这个 shell 类型 → 破坏分层。模块函数让 core 保持纯数据,语义与文档一致(签名以实现为准,README §0 允许)。

2. **删冗余 `Work.room_existed`**(见 0002 复盘):`room_existed` 恒等于 `work.room is not None`,reduce 用后者即可。

3. **`Card` 用 frozen+slots dataclass 而非 Pydantic**:core 域是 dataclass(models.md);牌作为值对象要可哈希(进 set/dict),frozen 合适。

## C. 已核对一致(无需改)

- **Command 全集**:与 [core.md](../core.md) 命令表 15 条逐条对上(JoinRoom 带 room/uid/loaded;其余不带 room;系统命令 origin=None 另带 nick)。系统命令同时有 `origin=None` 和 `nick` 是**有意**的(error.md L112-114:origin 决定错误回发给谁,nick 是游戏目标)。
- **Event A/B 组**:Broadcast/Personal/Persist(A)+ TurnChanged/ClearAction(B),与 architecture.md「Event 类别」一致。
- **四套状态机 + 转移表**:与 core.md 状态机表一致;补的 `(SITTING_IN,OFFLINE)`/`(OFFLINE,SITTING_IN)` 两条是修旧 enums.py 的漏(就座未 ready 者断线/重连)。
- **工作副本回滚 / 目标房解析表**:`_target_room` 与 storage.md 解析表逐行对上;commit 增/删/替换三路齐全。

## D. 遗留问题 / 待 P1+ 决议(记下别忘)

1. **`TurnChanged.timeout_s` 归属未定**:connection.md 的 dispatch 让事件带 `timeout_s`,意味着 core 产出它(core 读 `gameconfig.ACTION_TIMEOUT`)。但让 **Timer 自己读 config** 更干净(core 不碰基础设施配置)。**P3 接 timer.md 时定**;若决定 Timer 读 config,则 `TurnChanged` 不加 `timeout_s`(现状),并在 timer.md 注明。

2. **`ErrorCode` 是前瞻集合,未全部被使用**:当前枚举里多数码还没有 reduce 分支产出(P1 才写)。error.md 明确「随业务补充、后续配置化」。**纪律**:P1 写每个 reduce 臂时确认/增删对应码,别让枚举里堆死码。已先删掉无明确用处的 `INVALID_COMMAND`/`SEAT_EMPTY`。`CANT_CHANGE_NICK_IN_ROOM` 属 REST(P7),本期不放 core errors。

3. **`events.py` 的 `ServerMessage`/`PersistPayload` 是占位基类**:真身是 wire 的 Pydantic(P6,wire.md)与 db 的 Pydantic(P4,db.md)。届时 `Broadcast.msg`/`Personal.msg` 收紧为 `ServerMessage`(wire)、`Persist.payload` 收紧为 `PersistPayload`(db)。models.md 已许可 core import wire Pydantic,不破不变量 1。

4. **`Command` frozen+slots 继承**:基类 `Command(origin)` + 子类加字段,在 3.12 工作正常(测试已验)。注意:**带默认值的命令字段**(如 `StartHand.deck=None`)必须排在无默认字段之后,否则 dataclass 报错——现状满足,P1 加字段时守住。

5. **`StartHand.seat` 与 `origin` 可能冗余**:core.md 命令表带 `seat`,但发起人身份已由 `origin` 定。P1 写 `_start_hand` 时确认 `seat` 是否真需要(可能是「在哪个座位点的开始」),不需要就精简 + 改 core.md。

## 改了哪些文件

- `app/core/errors.py`:大写取值 + `NO_SUCH_ROOM` + `detail` 默认值 + 删两个无用码。
- `app/core/domain.py`:`UserState.room: str`。
- `app/core/events.py`:`TurnChanged` 加 `acting_nick`。
- 23 测试仍全绿;core 纯度校验通过。

## 待办 / 下一步

- 进 P1 时带上 D 的 5 条:`timeout_s` 归属、ErrorCode 随用随定、wire/db payload 收紧、StartHand.seat 取舍。
- 设计文档本轮**无需改**(漂移在代码侧)。
