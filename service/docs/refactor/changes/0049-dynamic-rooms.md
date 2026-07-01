# 0049 · 动态房间:谁都可创建(进房即建)+ 空房即销毁

日期:2026-07-01 · 范围:`app/core/commands.py`(`RoomCreate` + `JoinRoom.create`)、`app/core/reduce.py`(`_join_room` 建房 + `reduce()` 空房销毁归一)、`app/shell/receiver.py`(`_build_join` 盖建房配置)、`app/shell/lifespan.py`(`build_dev_world` 改空)、`app/gameconfig.py`(DEV_* 注释语义)、`tests/core/`+`tests/shell/`、`docs/core.md`/`lobby.md`/`storage.md`/`connection.md`/`TODO.md`。

## 背景 / 为什么(用户新设计)

用户明确房间设计:**谁都可以创建房间;创建后只要没玩家在房间就消失**。这把 core.md §房间生命周期(61-65)/ storage.md(51/63-64)里一直标「v1 静态预置 / 动态建房 future」的机制**转正为唯一模型**:无静态预置房,所有房**由进房动态创建、空则销毁**。设计文档已把机制写好(JoinRoom 到不存在的房 → `checkout` 给 `work.room=None` → reduce 建房 → `commit` 插入;最后一人离开 → reduce 置 `work.room=None` → `commit` 销毁),`commit`/`checkout`(shell/world.py)**早已支持增/删**——缺口纯在 reduce。

## 关键设计决策

1. **进房即建,无 `CreateRoom` 命令、无房主**:「谁都可以创建」= 对一个不存在的房名发 `JoinRoom` 即把它创建出来(与去房主/peer 一致,0044)。不引入注册表级 `CreateRoom`(它会有「建完即空 → 立即销毁」的悖论);创建与加入是同一动作。**创建者无特权**,建后任何在房成员可 `SetSmallBlind`/`SetBuyIn` 调参(已落地)。
2. **建房配置由 shell 经命令带,core 不 import config**:core 不知盲注/座位默认值。`JoinRoom` 加 `create: RoomCreate | None`(`RoomCreate(small_blind, buy_in, seats)`);**`None` 默认 = 不带建房配置**(非魔法数,故 core 干净、且「加入已存在房」的构造无需改)。Receiver `_build_join` 从 `gameconfig`(DEV_SMALL_BLIND/DEV_BUY_IN/DEV_SEATS,复用为「新建房默认配置」)盖 `create`。reduce 仅在**建房**(`work.room is None`)时用 `create`;加入已存在房时忽略它。config 边界(seats≥2 等)由 gameconfig `Field` 兜(同 SetSmallBlind 的 shell-bounds 范式)。
3. **wire 不变**:客户端仍只发 `join_room{room}`(建房配置不进报文,shell 盖);无 `wire.gen.ts` 变更、无 codegen 改。建房时用默认配置、建后调参,足够;「建房自定盲注」是 trivial future(往报文加 create 字段)。
4. **空房销毁 = 一处顶层归一**:`reduce()` 包一层——成功命令后若目标房 `users_in_room` 为空,置 `work.room=None`(commit 销毁)。**集中守「已提交的房永不为空」不变量**,一处覆盖 `LeaveRoom`/`Cleanup`/手尾 `_finalize_hand` 驱逐**所有**清空路径,不与 `_evict` 内部耦合、且在所有 mutation 之后跑(无顺序脆弱)。`Disconnect`(标 OFFLINE 留房)/ 起身(→WATCHING 留房)不清空 → 不销毁。
5. **销毁时序守恒 + 隐私沿用现有**:`_evict`/`_finalize_hand` 已「退座位筹码回全局(`Persist(PointsWrite)`)→ 移出 → `del users`」;销毁只在其后。手尾若全员离场清空 → 房销毁,但 `Persist(HandRecordWrite)` 仍落库(Persist 事件与房存亡无关)、离场者仍收 `Personal(UserLeft)` 回执;`Broadcast(HandEnded)` 到已销毁房被 dispatch 容错跳过(connection.md;无人可收本就无害)。
6. **无静态预置房**:`build_dev_world()` 返回空 `World`(rooms={});dev 用户连接后 `join_room{"dev"}` 创建「dev」,全走后即销毁、再进再建。`DEV_ROOM` 保留为「dev 建议房名」。

## 打算改什么(开工前)

- `app/core/commands.py`:新增 `RoomCreate(small_blind,buy_in,seats)`(frozen);`JoinRoom` 加 `create: RoomCreate | None = None`。
- `app/core/reduce.py`:`_join_room` 把 `work.room is None` 从 `NO_SUCH_ROOM` 改为**建房**(需 `cmd.create`,否则 `NO_SUCH_ROOM`);`reduce()` 重构为 `_dispatch` + 顶层空房销毁归一。
- `app/shell/receiver.py`:`_build_join` 构 `JoinRoom(..., create=RoomCreate(gameconfig.DEV_SMALL_BLIND, DEV_BUY_IN, DEV_SEATS))`。
- `app/shell/lifespan.py`:`build_dev_world()` → `World()`(空);更新注释/日志。
- `app/gameconfig.py`:DEV_SMALL_BLIND/DEV_BUY_IN/DEV_SEATS 注释改「新建房默认配置(dev)」。
- 测试:重写 `test_join_room_no_such_room` → `test_join_room_creates_room`;新增空房销毁 core 测(leave 最后一人销毁 / cleanup 最后一人销毁 / 手尾驱逐清空销毁 / 非最后一人不销毁);修 shell 测(dev world 空、JoinRoom 带 create);跑全量看涟漪、修「假设空房仍在」的旧断言(属预期行为变更)。
- 文档:core.md(§房间生命周期转正 + JoinRoom 命令表行)、lobby.md(§房间从哪来 / §进出房 / §待定动态建房转正)、storage.md(去 future 措辞)、connection.md(§lifespan 去「按 ROOMS 预置」)、TODO.md(勾动态房)。

## 实际改了什么

- **`app/core/commands.py`**:新增 `RoomCreate(small_blind, buy_in, seats)`(frozen dataclass);`JoinRoom` 加 `create: RoomCreate | None = None`。
- **`app/core/reduce.py`**:① `reduce()` 拆为薄壳 + `_dispatch`——壳在 `_dispatch` 后做**空房销毁归一**(`err is None and work.room is not None and work.room_name is not None and not work.room.users_in_room → work.room=None`);② `_join_room` 把 `work.room is None` 从 `NO_SUCH_ROOM` 改为**建房**(`cmd.create` 存在则 `Room(seats=[None]*seats, small_blind, buy_in)` 装 `work.room`;`create=None` 才 `NO_SUCH_ROOM`)。**单房间约束(`ALREADY_IN_ROOM`)检在建房之前**——失败的 join 不建房。
- **`app/shell/receiver.py`**:`_build_join` 构 `JoinRoom(..., create=RoomCreate(gameconfig.DEV_SMALL_BLIND, DEV_BUY_IN, DEV_SEATS))`;import 加 `RoomCreate`。
- **`app/shell/lifespan.py`**:`build_dev_world()` → `World()`(空,无预置房);删未用的 `Room` import;更新注释。
- **`app/gameconfig.py`**:`DEV_SMALL_BLIND`/`DEV_BUY_IN`/`DEV_SEATS` 注释改「新建房默认配置」、`DEV_ROOM` 改「dev 建议房名(非预置)」。
- **测试**:新建 `tests/core/test_room_lifecycle.py`(5:建房 / leave 最后一人销毁 / 非最后不销毁 / cleanup 退筹销毁 / **手尾 `_finalize_hand` 驱逐清空销毁**—— A ALLIN + B 行动皆离桌,B auto-fold 结束本手、手尾同驱逐清空);`test_join_reconnect.py` 的 `test_join_room_no_such_room` → `test_join_room_absent_without_create_config_errors`(create=None 路径);修 shell 涟漪(`test_dev_db_e2e` 空 world 断言 + JoinRoom 带 create;`test_lifespan_drain` ×2 带 create;`test_receiver` 「不存在→建房」改写)。
- **文档**:core.md(§房间生命周期转正为动态唯一模型 + JoinRoom 命令表行)、lobby.md(§房间从哪来 / §进出房 / §架构契约 4 / §待定动态建房转正)、storage.md(启动例外去「预置静态房」)、connection.md(lifespan 步 4 空 world)、TODO.md(P1 余项加动态房 [x])。
- **wire 不变**:`join_room{room}` 报文不动、`wire.gen.ts` 无变、`gen_wire_ts --check` OK(建房配置不进报文,shell 盖)。

445 全绿(440→445,+5 建/销测,含涟漪修 5 处 shell/core 测)。

## 自 review

方法:除逐维自查外,跑 **3 维对抗 review 子代理工作流**(守恒/money · 销毁顺序/一致性 · 假阳性销毁/建房边界),各代理实读代码 + 实跑探针、默认先反驳候选。**3 agent 全 CLEAN、0 defect**(候选全被自身反驳)。逐维:

- **① 分层 / 不变量**:core 仍不 import config(建房配置经 `JoinRoom.create` 由 shell 盖,同 uid/loaded/started_at 范式);`reduce()` 顶层归一仍纯同步、只改工作副本;`case _` 防御臂保留。**守恒(对抗代理实证)**:销毁路径「先 `_release_seat` 退座位筹码回全局 + 建 `PointsWrite` → 再 `_evict` pop/del → 最后归一置 work.room=None」顺序正确;`Persist` 派发 room-independent(dispatch.py 无条件 `persist.put`),房销毁不吞 `PointsWrite`/`HandRecordWrite`;建房全空座(0 筹码)、joiner 装 `points=loaded`(载入非铸造),不 mint/drop。实测手尾销毁路径 80 入 = 80 退全局。
- **② 代码↔文档同步**:core.md §房间生命周期转正 + JoinRoom 命令表行;lobby.md/storage.md/connection.md 去「静态预置」;gameconfig DEV_* 注释改「新建房默认配置」。命令签名 `JoinRoom(...create?)` 与文档一致。
- **③ 文档↔文档一致**:core.md「创建/销毁机制」↔ storage.md「checkout 给无房副本 / commit 增删」↔ lobby.md「进房即建 / 空则销毁」互指一致;connection.md lifespan「空 world」与 storage.md「启动 rooms 空」一致;修 core.md 一处重复 Disconnect bullet(edit 残留)。
- **④ 数据模型**:`RoomCreate` 三 int 忠实;`JoinRoom.create: RoomCreate | None = None`——`None` 默认非魔法数、语义「不带建房配置」,使「加入已存在房」构造零改。**建房 bounds(对抗代理点名)**:core **不**再校验 `seats/small_blind/buy_in` 越界,信 shell——**信任边界airtight**:wire `join_room` 报文**无** create 字段、`to_command(JoinRoom)` raise,core `JoinRoom.create` 唯一构造点是 `receiver._build_join`(从 `gameconfig` 盖,`DEV_SEATS: Field(ge=2)` 等 bounds),客户端无法喂越界 create。已在注释/变更记录记为**接受的设计**(防御纵深:core 信 shell 兜 create bounds,同信 shell 兜 room-config bounds 0043)。
- **⑤ 规范合规**:建房/销毁/归一各带「为什么」注释;无裸字面量(座位/盲注来自 gameconfig);删 lifespan 未用 `Room` import。
- **⑥ 测试充分**:5 新建/销测覆盖建房 / leave 最后一人 / **非最后不销**(防「一律销毁」变异)/ cleanup 退筹销(钉死退筹先于销毁)/ **手尾驱逐清空销**(最难:ALLIN+行动皆离桌);`create=None → NO_SUCH_ROOM` 保留独立测;涟漪修 5 处**真反映新行为**(dev world 空 / 建房需 create / receiver 建房)。**对抗代理另实证** OFFLINE-only 房不销毁、ALREADY_IN_ROOM 先于建房、reconnect/起身不销毁。445 全绿。
- **⑦ 流程账本**:打算↔实际一致(无偏离);TODO P1 余项加动态房 [x];提交将引用 0049、全英文。

**对抗核实存活 / 采纳 / 驳回**:3 lens 共约 15 候选缺陷**全被反驳**(0 存活)——守恒/顺序/假阳性销毁/建房越界/preset KeyError/reconnect 销毁 等逐一驳倒。**0 真 bug、0 代码改动**;review 兑现「高风险 core 变更须对抗验证」,本批以 3 独立视角 + 实跑探针确认「空房销毁」在守恒/顺序/边界三面无洞。唯一记录项:core 不兜 create bounds(信任边界airtight,接受)。

## 待办 / 下一步

- 建房自定盲注/座位(往 `join_room` 报文加 create 字段,让创建者设参)—— 本批用默认 + 建后调参绕过。
- `LobbyBroadcast`:建房/销房时推大厅列表增量(lobby.md 待定;v1 前端轮询 GET /lobby/rooms)。
- 房名冲突/命名规则、房数量上限(反滥用)—— 本规模(内网 ≤20)暂不设。
