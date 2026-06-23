# 0017 · W 阶段:wire 首批协议(Pydantic 单一事实源 + codegen TS)

日期:2026-06-23 · 范围:新建 `app/wire/`(`server.py`/`client.py`)、`app/core/reduce.py` 投影改产 wire DTO、删 `app/core/messages.py`、`app/core/events.py` 改 `msg` 类型、`tests/core/*` 改 import、`service/scripts/gen_wire_ts.py` 代码生成器、`frontend/src/types/wire.gen.ts` 生成产物、`tests/wire/` 漂移守门、文档同步(`wire.md`/`models.md`/`TODO.md`)。承 [0016](0016-replan-wire-first.md) 的 W 阶段。

## 背景 / 讨论

0016 把 wire 协议(原 P6)前置为下一步,解锁前端。现状:reduce 已产出 12 个语义快照载荷(`core/messages.py` 纯 frozen dataclass,挂 `events.py` 的 `ServerMessage` 占位基类),命令全集在 `core/commands.py`。W 阶段把**已落地**的消息/命令升级为 wire 的 Pydantic 可辨识联合(单一事实源),codegen 出 TS,前端弃手写漂移类型。

## 决策(批判性思考 + 与文档对齐)

1. **reduce 直接产 wire Pydantic DTO(收编 `core/messages.py`),不走 0016 的回退**。0016 预留「若 Pydantic 在 core 纯单测有摩擦则回退为 messages.py 保留 + dispatch 投影」。实测摩擦只有一处且很轻:Pydantic `BaseModel` 不支持位置构造,reduce 里 `PlayerView(...)`/`ShowdownReveal(...)`/`NickAmount(...)` 三处位置构造改成关键字即可。其余(字段访问、`isinstance`、`hasattr` 隐私断言)对 Pydantic 照常工作。故按 0016 主路径执行,**不回退**。依据 [models.md](../../models.md)「core 可 import wire Pydantic DTO」。

2. **隐私 = 结构性缺位(structural absence),而非 `field_serializer` 默认隐藏**。[wire.md](../../wire.md) 原文机制是「用 `field_serializer` 把 `hole_cards`/`deck` 设默认隐藏」。但本批**没有任何「持有底牌却要隐藏」的 DTO**:`deck` 不出现在任何 `ServerMessage`;`hole_cards` 只在两个**揭示点** DTO 显式携带(`HoleCards` 私发本人、`ShowdownReveal` 摊牌揭示未弃牌者)。广播类 DTO **结构上无** `hole_cards`/`deck` 字段——这正是 `core/messages.py` 现有做法,也是 `tests/core/*` 的 `not hasattr(msg,"hole_cards")` 隐私断言所验证的。结构性缺位比 `field_serializer` 更强(字段根本不存在,序列化无从泄露)且更简。`field_serializer` 留给**未来**确需「内部持牌、序列化隐藏」的 DTO(如他人视角的 `StateSnapshot`,本批未落)。→ **同步改 `wire.md` 隐私机制措辞**(把「默认 `field_serializer` 隐藏」修正为「揭示点 DTO 显式携带、其余结构性无此字段;`field_serializer` 为未来留」),满足 wire.md 契约 #5 的**结果**(底牌只在三处揭示)。

3. **codegen = 自包含 Python 生成器(无 node 依赖),不用 `pydantic2ts`**。0016/wire.md 指 `pydantic2ts`,但它 shell out 到 node 的 `json-schema-to-typescript`,**本机无 node/npm**(已核:`which node npm` 均无)。`pydantic2ts` 不可运行 = 阻塞。改为 `service/scripts/gen_wire_ts.py`:内省 wire 模型的 `model_fields` + 一张 Python 类型 → TS 映射,确定性地直接吐 TS(enums + `Card` + 嵌套模型 + 各消息 + 两个可辨识联合),写 `frontend/src/types/wire.gen.ts`(只读产物,带「DO NOT EDIT」头)。比走 JSON-schema 中间层产物更干净(无 `$ref`/`$defs` 噪声),且模型集小而扁,可控。→ **同步改 `wire.md` codegen 管线措辞**(工具从 pydantic2ts 改为自包含 Python 生成器;REST 的 `openapi-typescript` 待 P7,届时再议是否需 node)。

4. **codegen 漂移守门 = pytest 测试,而非外部 CI 配置**。wire.md 契约 #2「.py 改了不重生成 → CI 红」。无 node、仓内无显式 CI 配置;用 `tests/wire/test_codegen_uptodate.py`:在内存重生成 TS 与已提交的 `wire.gen.ts` 逐字节比对,不一致即测试红。这让既有 `pytest` 门槛(testing.md「CI 钩子:wire codegen 校验」)直接兜住漂移,无需 node/外部 CI。`gen_wire_ts.py --check` 同义供命令行/pre-commit 用。

5. **`ErrorMessage` 携带 `code` + `detail`**。wire.md 契约 #6「只回 `code` 不回文案」。`ErrorMessage` 主字段 `code: ErrorCode`(前端据它映射本地化文案);附带 `detail: str`(= `Err.detail`,开发上下文「谁/哪个座位/什么状态」,**非面向玩家的本地化文案**,供日志/调试)。前端渲染按 `code`,不直显 `detail`——不违背「文案前端按 code 映射」的精神。`wire/server.py` 提供 `ErrorMessage.from_err(err)`。

6. **wire 客户端报文字段名对齐 core 命令字段**(`seat`/`amount`/`status`/`action`/`bet_amount`),使 `to_command` 1:1 平凡映射。旧 [wsm_schemas.py](../../../app/pokertable/wsm_schemas.py) 的 `seat_number`/`buy_in`/`user_status` 是参考、被取代;wire 是全新单一事实源、无既有消费方,故取与命令一致的名。

7. **客户端→命令的墙钟边界**:`StartHand` 命令需 `started_at`(墙钟,shell 盖,core 不读钟),但**不在** wire 报文里。故 `to_command(msg, origin, now)` 带 `now`(Receiver 每帧已有的 shell 墙钟);仅 `StartHand` 用它,其余忽略。这把「读墙钟」钉在 shell/Receiver 边界,core 仍只收到带好时间戳的命令(commands.py `StartHand.started_at` 语义不变)。`deck` 经 wire 恒为 `None`(生产不注入),走命令默认。

8. **前端 mockup 暂不改(偏离 0016/TODO 的「删 poker.ts」)**。批判性核查发现:`frontend/src/types/poker.ts` **不是**纯协议类型文件,而是 **UI mockup 聚合类型**(`Player.name/chips/cards/isActive`、`GameState`、`GameAction`)+ `utils/poker.ts` 的**前端本地 mock 牌局逻辑**(`createDeck`/`shuffleDeck`/`evaluateHand`)。本批 `wire.gen.ts` 提供的是**协议面**(`Card{rank,suit}`、`PlayerView`、各消息、enums),**没有**也不该有 `Player`/`GameState`(那些是 UI 聚合,来自尚未设计的 `StateSnapshot`,不在本批)。前端无 WS client、无真消息流;此刻删 poker.ts、改组件 import wire.gen.ts 只会**破坏 mockup 而无替代**(Card 形状不同、缺 Player 类型)。→ **本批只生成 `wire.gen.ts`(解锁前端 devs 按真类型写 WS client),不删 poker.ts、不改 mockup 组件**;把「前端消费 wire.gen.ts + 删 poker.ts」拆为后续「前端 WS client 集成 + StateSnapshot」工作单元。**同步调整 TODO** 的 W「前端」项措辞。

## 打算改什么(开工前)

- **新建 `app/wire/server.py`**:`ServerMessage(BaseModel)` 基类(frozen)+ 嵌套模型 `PlayerView`/`ShowdownReveal`/`NickAmount` + 9 个消息(`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerActed`/`HandShowDown`/`HandEnded`/`UserStatusChanged`/`UserLeft`/`PlayerBoughtIn`)+ `ErrorMessage`(`from_err`)+ `SERVER_MESSAGES` 注册表(供 codegen)。字段**逐字照搬** `core/messages.py`(reduce/tests 按名访问,零改面)。
- **新建 `app/wire/client.py`**:`ClientMessage(BaseModel)` 基类 + 6 个报文(`SitDown`/`BuyIn`/`SetUserStatus`/`LeaveRoom`/`StartHand`/`PlayerAction`)+ `CLIENT_MESSAGES` 注册表 + `parse(data) -> ClientMessage`(TypeAdapter 可辨识联合)+ `to_command(msg, origin, now) -> Command`(match,身份盖 origin、墙钟盖 now)。
- **改 `app/core/events.py`**:`Broadcast.msg`/`Personal.msg` 类型从本地占位 `ServerMessage` 改引 `app.wire.server.ServerMessage`;删本地 `ServerMessage` 占位基类;`PersistPayload` 保留(records.py 用、不上 wire)。
- **改 `app/core/reduce.py`**:import 从 `app.core.messages` 改 `app.wire.server`;三处位置构造(`PlayerView`/`ShowdownReveal`/`NickAmount`)改关键字。
- **删 `app/core/messages.py`**。
- **改 `tests/core/*`(5 文件)**:`from app.core.messages import ...` → `from app.wire.server import ...`(字段同名,断言面不变)。
- **新建 `service/scripts/gen_wire_ts.py`**:生成器 + `--check`;**生成 `frontend/src/types/wire.gen.ts`**。
- **新建 `tests/wire/test_codegen_uptodate.py`**:漂移守门。
- **文档**:`wire.md`(隐私机制 / codegen 工具 / ErrorMessage)、`models.md`(messages.py→wire/、records.py 保留)、`TODO.md`(W 勾项 + 前端项调整)。

## 自 review

方法:按 [review.md](../../review.md) 7 维逐维过 + 跑了一次**对抗式 review 工作流**(7 维各派 1 独立审查者 → 每个候选发现再派 1 独立反驳者,驳不倒才算)。结论:**①–⑥ 全清(0 发现存活反驳)**,⑦ 抓到 2 项——均为本变更记录的「自 review」/「待办」段当时仍是占位(预期的提交门槛项,现填),非代码缺陷。逐维记录:

- **① 分层 / 不变量**:`grep app/core` 无 `fastapi/sqlalchemy/sqlmodel/websockets/app.shell`;唯一新增跨层 import 是 `app.core.{events,reduce}` → `app.wire.server`,正是 [models.md](../../models.md) 允许的「core 可 import wire DTO」例外。无循环:`events→wire.server→core.{cards,enums,errors}`、`reduce→events+wire.server`,wire 不回指 core.events/reduce(152 测试全绿即无 runtime cycle 复证)。reduce 投影路径不 raise(沿用 helper 风格)。所有 wire DTO 为**值类型**(int/str/StrEnum/frozen Card/frozen 嵌套模型),`players`/`board`/`cards`/`reveals` 皆由 `p.seat_position`/`p.hole_cards` 等快照值构造,**不持域活引用**(守不变量 7)。
- **② 代码↔文档同步**:三处实现偏离均同次改文档——隐私=结构性缺位(非 `field_serializer`)、codegen=自包含 Python 生成器(非 `pydantic2ts`)、`ErrorMessage` 带 `detail`,均已落 [wire.md](../../wire.md);`core/messages.py` 删除 + 投影改产 wire DTO 已落 [models.md](../../models.md);[TODO.md](../TODO.md) W 段勾项与实际产出一致。
- **③ 文档↔文档一致**:计数核对——server 9 消息 + `ErrorMessage`、client 6 报文,与 .py 注册表 `SERVER_MESSAGES`/`CLIENT_MESSAGES` 一致;新增链接(0017↔wire.md/models.md/TODO,wire.md→scripts/tests)指向真实路径;0016「回退预案」在本篇决策 1 显式不触发并说明。
- **④ 数据模型正确性**:`type` 判别量在生成 TS 中**修为必填**(原 Pydantic 默认值致 `is_required()=False`、误标 `type?`,会破坏 TS 可辨识联合收窄,也与 client 入站 Pydantic 必须据 `type` 判别相悖)——已特判恒必填。`int|None`→`number | null`、`tuple[Card,Card]`→`[Card, Card]`、`tuple[X,...]`→`X[]` 经产物核对正确;client 字段可选性(`seat?`/`bet_amount?`)= Pydantic 非必填,正确;`to_command` 6 报文字段无丢失(测试 `test_parse_and_to_command_maps_every_client_message` 锁等值);`StartHand` 的 `started_at=now`/`deck=None` 边界由 shell 盖钟、生产不注牌,正确。未把不可能态变可表达、未过严卡合理用法。
- **⑤ 规范合规**:每个 wire DTO 字段带中文注释(守 code-comment-style);命名对齐 core 命令(`seat`/`amount`/`status`/`action`/`bet_amount`)使 `to_command` 平凡;无魔法数;`gen_wire_ts.py` 无死分支(未含投机的 `list`/未用臂,未知注解显式 `raise TypeError` 失败响亮);`ErrorMessage.from_err` 沿 `Err` 风格。
- **⑥ 测试充分**:隐私**双层**——core 测结构性(`hasattr`)、wire 测**序列化产物**(`model_dump` 无 `hole_cards`/`deck`/`cards`);drift guard 经 tamper 实测**真会红**(非真空真);`parse`/`to_command`/`from_err`/可辨识联合拒绝(未知 type/缺判别量/缺必填)全覆盖;`test_client_registry_covered_by_to_command` 防新增报文漏测。152 全绿(144 core + 8 wire 协议 + drift guard;其中 wire 目录 8 测试)。
- **⑦ 流程账本**:打算 7 项全落(server/client/reduce 迁移/删 messages.py/codegen/drift 测试/文档);frontend 项按决策 8 显式延后并改 TODO;提交将引用 `0017`、全英文。对抗式 review 2 确认即本两段——现填补。

> 批判性自评:决策 1(reduce 直产 Pydantic)实测仅位置构造一处轻摩擦,未触发 0016 回退,已验。决策 3(自包含生成器)虽偏离 pydantic2ts,但其漂移守门(pytest 逐字节)在无 node 下**更可执行**;若日后引入 node 工具链,可平替为 pydantic2ts 并保留本 pytest 守门做双保险。

## 待办 / 下一步

- **已完成且测过**:`app/wire/server.py`(9 消息 + `ErrorMessage.from_err` + 3 嵌套值对象 + 注册表)、`app/wire/client.py`(6 报文 + `parse` + `to_command`)、reduce 投影改产 wire DTO、删 `core/messages.py`、`events.py`/5 test 文件 repoint、`scripts/gen_wire_ts.py`(+`--check`)、生成 `frontend/src/types/wire.gen.ts`、`tests/wire/`(drift guard + 8 协议测试);152 全绿、`gen_wire_ts.py --check` 干净。
- **本批延后(有意,已记决策 8)**:前端消费 `wire.gen.ts` + 删 `poker.ts` + 改 mockup 组件 → 归后续「前端 WS client 集成 + StateSnapshot」单元(本批无 `Player`/`StateSnapshot` wire 类型,且前端无 node 不可构建验证)。
- **随后续模块增量补协议切片**(0016 贯穿项):`JoinRoom`/`Connect`/`StateSnapshot`、免盲投票、`RoomChat`/`ChatMessage`、`Set*Blind`、REST(`openapi-typescript`,P7)。每落一个补该模块 wire 切片 + 重 codegen。
- **下一步执行序**:按 0016 重排表,W 完 → **D 阶段(最小明文 dev shell + 端点)**,串起已实现 reduce 让前端真连联调;`gen_wire_ts.py --check` 可接入 D 阶段 pre-commit 钩子(若届时引入)。
