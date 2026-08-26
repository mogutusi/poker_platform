# wire 协议契约(治理,不是消息清单)

## 定位:这份文档不列消息

一句话:本文只管协议怎么治理,不列字段。

具体消息(字段、类型)只写在后端 Pydantic(.py),由 codegen 生成前端 TS,是单一事实源;不在本文或前端手写第二份——写进 md 必然和代码漂移。**前车之鉴**:[frontend/src/types/poker.ts](../../frontend/src/types/poker.ts) 曾手写过一份协议形状,很快就漂了两处——`Player.chips` 字段名对不上(后端叫 `points`),`GameState.phase` 枚举取值对不上(后端是 `pre_flop`/…);那几个接口后来被证实一个消费者都没有,已随死代码删除([0099](refactor/changes/0099-retire-the-mockup-types.md))。教训仍然成立:手写第二份就会漂。

本文只定五件事:codegen 管线、消息形状约定、隐私红线、与 `Command`/`Event` 的对应、演进规则。

> 前端「怎么用」→ [wire-protocol-guide.md](wire-protocol-guide.md)。
>
> 前置:[architecture.md](architecture.md) 的「五种数据类型」、[auth.md](auth.md)(wire 明文是加密帧里的载荷)、[error.md](error.md)(`ErrorMessage`)。

## 单一事实源 + codegen 管线

一句话:后端 Pydantic(.py)是唯一真源,前端 TS 是只读产物(只消费,禁止手写/手改),漂移由测试守门。

**ws 消息**(`ClientMessage`/`ServerMessage`):Pydantic 模型 → TS 可辨识联合。

- 生成器是自包含 Python 脚本 [scripts/gen_wire_ts.py](../scripts/gen_wire_ts.py),内省 `model_fields` 直接吐扁平 TS,不依赖 node——本机无 node、`pydantic2ts` 不可运行,故自实现(见 [changes/0017](refactor/changes/0017-wire-first-batch.md))。

**REST**(查手牌/余额):FastAPI 出 OpenAPI →(`openapi-typescript`)→ TS。待 P7。

**共享词汇目录**:不是消息,但同走「单源 + codegen」。表情目录已落地([0035](refactor/changes/0035-emoji-implementation.md)):

- 源在 `app/wire/emoji.py`:`EmojiCode` 封闭枚举 + `EMOJI_CATALOG`。
- 生成器 `_emit_emoji_catalog` 无条件吐 `EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG` 进 `wire.gen.ts`。「无条件」是因为它不被任何消息引用,走不到 `_discover` 的 ref_set 断言路径。
- 供前端渲染 `[code]`:表情 token 后端纯透传(就在聊天正文 `text` 里),目录本身不作为消息字段、不新增 wire 字段(见 [messaging.md](messaging.md)「表情」);漂移由 `test_codegen_uptodate` + `test_emoji` 兜。

**漂移守门在 `pytest` 里**:改了 .py 不重新生成 → 测试红。(仓库没有 CI 也没装 pre-commit,靠跑测试和 [dev.md](dev.md) 的提交规约;见 [BUGS.md](refactor/BUGS.md) DEBT-1。)

- 守门测试是 [tests/wire/test_codegen_uptodate.py](../tests/wire/test_codegen_uptodate.py),在 `pytest` 里逐字节比对,无 node 也能跑;`gen_wire_ts.py --check` 是同义命令,供 pre-commit 用。
- 前端改动只能改 .py 再生成,不碰产物。

**.py 落点**:wire 模型集中在 [app/wire/](../app/wire/)(`server.py`/`client.py`)。它取代原型的 `pokertable/wsm_schemas.py`(后者 0027 拆除);与域模型物理分开,理由见「wire DTO ≠ 域模型」。

## 每条消息必须遵守的形状(约定,非清单)

一句话:五条形状约定,新消息照抄即可。标「决策(可改)」的是可以重新讨论的选择。

1. **可辨识联合**:每条消息带一个 `type` 字面量(如 `"player_action"`)。Pydantic 用它做 `Discriminator`,TS 1:1 得到 discriminated union;分两个顶层联合:`ClientMessage`(进)/ `ServerMessage`(出)。
2. 决策(可改)· **扁平信封**:字段平铺在消息上(`{type, seat_number}`),不套 `{type, payload:{…}}`。可辨识联合对扁平结构最顺,少一层解包;原型亦扁平,沿用。
3. 决策(可改)· **`snake_case` 字段**:wire 上一律 snake_case,与 Python/JSON 一致,不为前端改 camelCase。真要 camelCase,只能改生成器配置,不手改产物。
4. **强类型,无裸结构**:每条消息是一个 Pydantic 模型,字段带类型。枚举(`UserStatus`/`HandStatus`/…)直接用后端 enum,前端拿到同名联合,杜绝 magic string。
5. 决策(可改)· **身份不进报文**:`ClientMessage` 不带发送者身份,只装动作参数。
   - 身份是连接:握手时绑定的会话 nick(见 [auth.md](auth.md))。Receiver 收帧后盖 `origin=nick` 进 `Command`;目标房由 `world.users[origin].room` 推定(见 [lobby.md](lobby.md))。
   - 例外:若出于纵深防御在加密封套内带了 `id`,服务器只校验它等于会话身份,绝不当身份来源。

## 与 Command / Event 的对应(不重复定义,见 architecture.md)

一句话:客户端消息 1:1 变 `Command`,服务器消息是 `Event` 的载荷。

| wire | 方向 | 对应 core 类型 |
|---|---|---|
| `ClientMessage` | 客户端 → 服务器 | 1:1 映射成一个 `Command`;系统命令 `Timeout`/`Cleanup`/`Connect` 无报文,由 shell 产生 |
| `ServerMessage` | 服务器 → 客户端 | 是 `Event` 里 `Broadcast`/`Personal` 的 payload;`ErrorMessage` 也是 `ServerMessage`,由 `Err` 在 wire 转成 |

边界澄清照 architecture.md:Command ≠ Message(系统命令没报文);Event ≠ Message(Event 是 core→shell 的内部信封);Err ≠ Message(到 wire 才转 `ErrorMessage`)。

## 隐私红线(wire 上)

一句话:底牌和牌堆靠「DTO 里根本没这个字段」来防泄露,只有三处显式公开。

- **`hole_cards` / `deck` 默认不出现在任何 `ServerMessage` 序列化里**,机制是结构性缺位:广播类 DTO 根本没有这些字段,字段不存在,`model_dump` 就无从泄露;比「用 `field_serializer` 默认隐藏」更强、更简(见 [changes/0017](refactor/changes/0017-wire-first-batch.md))。
- `StateSnapshot`(0022)同理:由 reduce 逐收件人构造,`your_hole_cards` 只装收件人自己的底牌;在手玩家投影为 `players`,而 `PlayerView` 结构上无 `hole_cards`。
- `field_serializer` 至今未用,只有「一个 DTO 内部持全牌、按视角序列化裁剪」的场景才需要它。

**底牌只在三处显式公开:**

| 场合 | 携带底牌的字段 | 范围 |
|---|---|---|
| `Personal(HoleCards)` | `HoleCards.cards` | 发本人 |
| `Broadcast(HandShowDown)` | `ShowdownReveal.hole_cards` | 摊牌揭示未弃牌者 |
| `Personal(StateSnapshot)` | `StateSnapshot.your_hole_cards` | 只含收件人自己的底牌 |

其余 DTO 结构上无此字段。

对应 [core.md](core.md) 不变量 3:底牌/牌堆隐私是 core 把关、wire 兜底,两层都不能漏。

## 文案不进协议

一句话:协议只回机器可读 `code`(`ErrorMessage.code: ErrorCode`,见 [error.md](error.md)),给玩家看的话由前端按 `code` 映射,不进报文。

- 加一种语言只改前端文案表,不动 .py。
- `ErrorMessage` 另带 `detail: str`(= `Err.detail`),内容是开发上下文:谁、哪个座位、什么状态;供日志/调试,前端不直显。

## wire DTO ≠ 域模型

一句话:`ServerMessage` 是「客户端要渲染什么」的视图,不是 core 的 `World`/`Hand` 直接序列化。

两者必须物理分开:

- 域模型含 core 内部字段(`epoch`/`deck`/`contributed`/`start_time` 等),其中不少不该上 wire——有隐私问题,也有内部计数。
- wire DTO 只挑客户端需要的字段,且按「谁能看」裁剪。
- 由 reduce 在产出 `Event` 时把域状态投影成 wire DTO。投影出的是快照值,不持 `world` 活引用,守不变量 7。

## 演进约定

一句话:只做加性演进,不设版本号,真要破坏就前后端一次改完。

- 决策(可改)· **加性演进优先**:加新消息类型、加可选字段(给默认值),不改/删既有字段语义。前端忽略不认识的 `ServerMessage` type,即向后兼容。
- **不引入协议版本号**(当前):靠「codegen 同源 + 加性演进」维持一致,前后端总是同一次构建的产物,无跨版本协商;真出现破坏性变更再议版本机制。
- **破坏性变更 = 一次性同步**:本规模前后端同仓同发。一次改完 .py + 重生成 + 前端跟随,不做灰度兼容。

## 契约(必须守住)

1. 消息只在后端 Pydantic 写一份,TS 是 codegen 产物;前端禁止手写/手改 wire 类型。
2. codegen 有守门测试:.py 变更必须连带重新生成,产物与源一致(由 `pytest` 兜,非 CI)。
3. 每条消息带 `type` 字面量,构成可辨识联合;扁平信封、`snake_case`、强类型、用后端 enum。
4. 身份不进报文:身份来自连接,报文只带动作参数。
5. `hole_cards`/`deck` 默认隐藏,仅 `HoleCards`/`HandShowDown`/自己的 `StateSnapshot` 显式揭示。
6. 只回 `code`、不回文案;文案前端按 `code` 映射。
7. wire DTO 与域模型物理分开,由 reduce 投影产出,带快照值。

## 待定 / 在代码里(不在本文)

**具体消息清单**:首批已落 [app/wire/](../app/wire/),0017 的范围是 server 9 消息 + `ErrorMessage`、client 6 报文;各字段写在 .py,随实现增量补(见 [changes/0016](refactor/changes/0016-replan-wire-first.md))。

**`StateSnapshot`**:已落地(0022),字段见 [app/wire/server.py](../app/wire/server.py)。

- 隐私 = 结构性缺位 + `your_hole_cards`(见「隐私红线」)。
- 由 reduce 逐收件人投影,入口是 `_connect`(重连)和 `_join_room`(进房)。
- `HandShowDown` 已落,带 `board` + `reveals`。

**REST 的 `openapi-typescript`**:待 P7(ws 消息的 codegen 已落)。
