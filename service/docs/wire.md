# wire 协议契约(治理,不是消息清单)

## 定位:这份文档不列消息

**具体消息(字段、类型)只写在后端 Pydantic(.py),由 codegen 生成前端 TS——单一事实源,绝不在本文或前端手写第二份。** 把消息清单写进 md 必然和代码漂移(现有 [frontend/src/types/poker.ts](../../frontend/src/types/poker.ts) 的 `chips`/`phase` 已和后端 enum 漂移,就是反例)。

所以本文只定**协议怎么治理**:单一事实源 + codegen 管线、每条消息必须遵守的**形状约定**、隐私红线、与 `Command`/`Event` 的对应、演进规则。**字段长什么样去看 .py**,本文一个字段都不写。

> 前置:[architecture.md](architecture.md) 的「五种数据类型」(wire 在其中的位置)、[auth.md](auth.md)(wire 明文是加密帧里的载荷)、[error.md](error.md)(`ErrorMessage`)。

## 单一事实源 + codegen 管线

```
后端 Pydantic(.py) ──codegen──▶ 前端 TS 类型(只读产物)
  唯一真源                         前端只消费,禁止手写/手改
```

- **ws 消息**(`ClientMessage`/`ServerMessage`):Pydantic 模型 →(`pydantic2ts`)→ TS 可辨识联合。
- **REST**(查手牌/余额):FastAPI 出 OpenAPI →(`openapi-typescript`)→ TS。
- **生成步骤进 CI / pre-commit**:改了 .py 不重新生成 → CI 红。前端改动只能改 .py 再生成,不碰产物。
- **.py 落点**:wire 模型集中在一个协议模块(取代旧 [wsm_schemas.py](../app/pokertable/wsm_schemas.py)),与域模型(core 的 `World`/`Hand`/…)**物理分开**——域模型是 core 权威状态,wire DTO 是对外报文,两者独立演进(见「wire DTO ≠ 域模型」)。

## 每条消息必须遵守的形状(约定,非清单)

1. **可辨识联合(discriminated union)**:每条消息带一个 **`type` 字面量**(如 `"player_action"`),Pydantic 用它做 `Discriminator`,TS 1:1 得到 discriminated union。分两个顶层联合:`ClientMessage`(进)/`ServerMessage`(出)。
2. **决策(可改)· 扁平信封**:字段**平铺**在消息上(`{type, seat_number}`),不套第二层 `{type, payload:{…}}`。理由:Pydantic/TS 的可辨识联合对扁平结构最顺,少一层解包;现有 [wsm_schemas.py](../app/pokertable/wsm_schemas.py) 已是扁平,沿用。
3. **决策(可改)· `snake_case` 字段**:wire 上一律 snake_case(与 Python/JSON 一致,前端按生成类型用),不为前端改 camelCase——生成器要转随生成器配置,不手改产物。
4. **强类型,无裸结构**:每条消息是一个 Pydantic 模型,字段带类型;枚举(`UserStatus`/`HandStatus`/…)直接用后端 enum,前端拿到同名联合,杜绝 magic string。
5. **决策(可改)· 身份不进报文**:`ClientMessage` **不带发送者身份**——身份是**连接**(握手时绑定的会话 nick,见 [auth.md](auth.md)),Receiver 收帧后盖 `origin=nick` 进 `Command`(目标房由 `world.users[origin].room` 推定,见 [lobby.md](lobby.md))。报文只装**动作参数**(座位号、金额、动作类型)。若出于纵深防御在加密封套内带了 `id`,服务器**只校验它等于会话身份**,绝不拿它当身份来源。

## 与 Command / Event 的对应(不重复定义,见 architecture.md)

| wire | 方向 | 对应 core 类型 |
|---|---|---|
| `ClientMessage` | 客户端 → 服务器 | **1:1** 映射成一个 `Command`(系统命令 `Timeout`/`Cleanup`/`Connect` **无报文**,由 shell 产生) |
| `ServerMessage` | 服务器 → 客户端 | 是 `Event` 里 `Broadcast`/`Personal` 的 **payload**;`ErrorMessage` 也是 `ServerMessage`(由 `Err` 在 wire 转成) |

边界澄清照 architecture.md:**Command ≠ Message**(系统命令没报文)、**Event ≠ Message**(Event 是 core→shell 的内部信封,Message 是信封里发给客户端的信)、**Err ≠ Message**(到 wire 才转 `ErrorMessage`)。

## 隐私红线(wire 上)

- **`hole_cards` / `deck` 默认不出现在任何 `ServerMessage` 序列化里**。用 Pydantic `field_serializer` 把它们设成默认隐藏(`model_dump(mode="json")` 出来已抹掉),同 [log.md](log.md) 的脱敏来源。
- **底牌只在三处显式公开**:`Personal(HoleCards)` 发本人、`Broadcast(HandShowDown)` 摊牌揭示未弃牌者、`Personal(StateSnapshot)` 里**只含自己的**底牌。这几处的消息**显式携带**底牌字段(不走默认隐藏),其余一律隐藏。
- 对应 [core.md](core.md) 不变量 3:底牌/牌堆隐私是 core 把关、wire 兜底,**两层都不能漏**。

## 文案不进协议

- wire 只回**机器可读 `code`**(`ErrorCode` 等,见 [error.md](error.md));面向玩家的中文/多语言文案**由前端按 `code` 映射**,不进报文。
- 这让协议与文案解耦:加一种语言只改前端文案表,不动 .py、不重生成。

## wire DTO ≠ 域模型

`ServerMessage` 是"客户端要渲染什么"的视图,不是 core 的 `World`/`Hand` 直接序列化。两者**必须物理分开**:

- 域模型含 core 内部字段(`epoch`/`deck`/`contributed`/`start_time` 等),其中不少**不该上 wire**(隐私、内部计数)。
- wire DTO 只挑客户端需要的、且按"谁能看"裁剪(他人底牌不给)。
- 由 reduce 在产出 `Event` 时把域状态**投影**成 wire DTO(快照值,不持 `world` 活引用,守不变量 7)。

## 演进约定

- **决策(可改)· 加性演进优先**:加新消息类型 / 给消息加可选字段,不改/删既有字段语义。可辨识联合天然支持:前端**忽略不认识的 `ServerMessage` type**(向后兼容),新字段给默认值。
- **不引入协议版本号**(当前):靠"codegen 同源 + 加性演进"维持前后端一致;前后端总是同一次构建的产物,不存在跨版本协商。真出现破坏性变更(改字段语义/删字段)再议版本机制。
- **破坏性变更 = 一次性同步**:本规模前后端同仓同发,破坏性改动就一次改完 .py + 重生成 + 前端跟随,不做灰度兼容。

## 契约(必须守住)

1. **消息只在后端 Pydantic 写一份**,TS 是 codegen 产物;前端禁止手写/手改 wire 类型。
2. **codegen 进 CI / pre-commit**:.py 变更必须连带重新生成,产物与源一致。
3. **每条消息带 `type` 字面量**,构成可辨识联合;扁平信封、`snake_case`、强类型、用后端 enum。
4. **身份不进报文**:身份来自连接(会话绑定),报文只带动作参数。
5. **`hole_cards`/`deck` 默认隐藏**,仅 `HoleCards`/`HandShowDown`/自己的 `StateSnapshot` 显式揭示。
6. **只回 `code`、不回文案**;文案前端按 `code` 映射。
7. **wire DTO 与域模型物理分开**,由 reduce 投影产出,带快照值。

## 待定 / 在代码里(不在本文)

- **具体消息清单**:`ClientMessage`/`ServerMessage` 全集、各字段——**写在 .py**,随 core 域模型敲定(**未写**)。
- **`StateSnapshot` / `HandShowDown` 的精确字段**:随域模型定,在 .py;本文只约定其**职责 + 隐私**(快照含自己底牌、摊牌揭示未弃牌者),不列字段。
- **codegen 脚本接 CI / pre-commit**:工具选 `pydantic2ts` + `openapi-typescript`(见上),脚本与钩子随实现补。
