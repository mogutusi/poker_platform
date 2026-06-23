# 0016 · 重排计划:wire 协议前置 + 明文 dev 端点解锁前端(加密信道推到最后)

日期:2026-06-23 · 范围:`docs/refactor/TODO.md`(阶段重排 + 详化)、`docs/refactor/README.md` §4(顺序指针)、本变更记录。**纯计划/文档,无代码改动。**

## 背景 / 讨论

前端要开始干活,需要**可消费的协议契约**(`ClientMessage`/`ServerMessage` 的 TS 类型 + 连接方式)才能并行。现状:

- 新 core 有「事实上的协议」但不可直接消费:`core/commands.py`(入站 `Command`,带 `origin`)+ `core/messages.py`(12 个出站载荷,纯 `@dataclass`、无 `type` 判别、非 Pydantic)。
- [wire.md](../../wire.md) 定的契约是**后端 Pydantic 可辨识联合 → codegen → TS(单一事实源)**;wire 模块尚未写(原排在 P6,偏后)。旧原型 [wsm_schemas.py](../../../app/pokertable/wsm_schemas.py) 已有可用的 Pydantic `ClientMessage`/`ServerMessage`,是新模块的参考(将被取代)。
- 前端 [types/poker.ts](../../../../frontend/src/types/poker.ts) 手写且已漂移(`chips`/`phase`/`value`、camelCase,与后端 snake_case/enum 对不上),须删、改用生成产物。

→ **结论:把 wire 协议(原 P6)前置为下一步**,解锁前端。核心规则已落到消息形状稳定(开局/动作/摊牌/座位/生命周期都已实现),前置可行。

## 决策(与用户对齐,Q&A)

1. **解锁边界 = 协议 + 明文 dev 端点**:出 wire 单一事实源 + codegen TS + 一个**最小明文 WS dev 端点**(跳过国密 SM4/HMAC 逐帧加密),接上已实现的 reduce,让前端**立即真连联调**。**国密安全信道(原 P5)推到最后**——它是「替换 dev 明文握手/帧」的一层,不阻塞功能联调。
2. **协议增量交付**:本批**只给已设计/已落地**的消息与命令;**之后每落一个模块,补该模块的 wire 协议切片 + 重新 codegen**,前端跟随。不一次性把未设计的(StateSnapshot/JoinRoom/免盲投票…)硬塞。
3. **wire DTO 收编 `core/messages.py`(单一事实源)**:`core` 允许 import wire 的 Pydantic DTO(构造 ≠ 序列化,见 [models.md](../../models.md) / [README §3](../README.md));故 **reduce 直接产 wire Pydantic DTO**,`core/messages.py` 临时 dataclass 被 `app/wire/` 取代——无双份、codegen 源即 reduce 输出。`core/records.py`(Persist 载荷 `PointsWrite`/`HandRecordWrite`)是 delayDB 写入项**不上 wire**,保留(P4 对齐 ORM)。

## 重排后的执行顺序

| 序 | 阶段 | 目标 | 备注 |
|---|---|---|---|
| ✓ | P0 / P1(主体) | core 基线 + 规则(开局/动作/摊牌/边池/局中生命周期/座位买入) | 已落 0002–0015 |
| **1** | **W · wire 首批协议** | 已设计消息/命令 → Pydantic 单一事实源 + codegen TS + 前端弃手写类型 | 解锁前端(对 mock 开发) |
| **2** | **D · 最小明文 dev shell + 端点** | GameLoop + dispatch + 明文 Connection/Receiver/Sender + Timer + Persist 桩 + dev 端点 | 前端真连联调;**无加密** |
| 3 | P1 余项 | 免盲投票(①.12-15)/等大盲(①.7-10)/RoomChat/JoinRoom+Connect+StateSnapshot/Set*Blind | **每项补协议切片** |
| 4 | 硬化 | P4 delayDB(双缓冲/drain)、shell 背压/顶替/重连 StateSnapshot、P7 lobby/REST/messaging | 每模块补协议切片 |
| 5 | 日志 / 配置收编 | gameconfig 收编(买入上下限等)、日志脱敏 | P8 子项前移随用随接 |
| **末** | **P5 · 国密安全信道** | 登录握手 + 逐帧 SM4+HMAC-SM3 + 密码哈希迁移 + K_user 轮换 | **替换 dev 明文层,最后做** |
| 末 | P8 · 收尾 | lifespan drain、端到端冒烟 | |

> 不变量与分层铁律不变(见 [coding_principle.md](../../coding_principle.md));重排只动**顺序**,不动**架构**。明文 dev 端点是临时脚手架,标 `dev-only`,P5 落地即替换。

## W 阶段详化(wire 首批协议)

- [ ] `app/wire/server.py`:`ServerMessage` 可辨识联合(Pydantic `BaseModel`)= 当前 `core/messages.py` 全集升级(加 `type` 字面量、扁平、snake_case、用 core enums):`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerActed`/`HandShowDown`/`HandEnded`/`UserStatusChanged`/`UserLeft`/`PlayerBoughtIn` + `ErrorMessage`(由 `Err` 转)。隐私 `field_serializer`:`hole_cards`/`deck` 默认隐藏,仅 `HoleCards`/`HandShowDown` 显式带。
- [ ] `app/wire/client.py`:`ClientMessage` 可辨识联合 = **已落地命令**的报文(身份不进报文、无 `origin`):`SitDown`/`BuyIn`/`SetUserStatus`/`LeaveRoom`/`StartHand`/`PlayerAction`。(`JoinRoom`/`Set*Blind`/`RoomChat`/投票随各模块补。)+ `parse`(client→`Command`,Receiver 盖 `origin=nick`)。
- [ ] reduce 投影改产 `app/wire` DTO,删 `core/messages.py`;`tests/core/*` 改 import(字段同名,断言面基本不变;`isinstance`/`hasattr` 隐私断言照旧)。
- [ ] codegen:`pydantic2ts`(+ REST 的 `openapi-typescript` 留 P7)→ `frontend/src/types/wire.gen.ts`(只读产物);脚本进 `service/`,钩子接 pre-commit/CI(改 .py 不重生成即红)。
- [ ] 前端:删 `frontend/src/types/poker.ts`(手写漂移),改 import `wire.gen.ts`。
- [ ] 协议指南:`wire.md` 已是治理;补「消息流时序 + dev 连接握手(明文)+ 错误码用法」薄页或并入 wire.md/connection.md,指向生成产物。

## D 阶段详化(最小明文 dev shell + 端点)

- [ ] `shell/gameloop.py`:`inbox` 串行 → `checkout` → `reduce` → `commit`/discard → `dispatch`(只 `put_nowait`);异常归一 `Err(INTERNAL)`。
- [ ] `shell/dispatch.py`:`Broadcast`(按 `world` 房成员 + `conns` 取连接,容错销毁房)/`Personal`/`Persist`(交 persist 桩)/`TurnChanged`·`ClearAction`(调 Timer)。
- [ ] `shell/connection.py`:`ConnectionManager`(register/unregister/is_current/get/顶替)+ `Connection`(**明文 `outbound`,无 `SecureChannel`**)。
- [ ] `shell/receiver.py`:**dev 明文握手**(`?nick=` 或最简 session,**无 MAC/加密**,标 `dev-only`)→ 登记(顶替)→ 起 Sender → `Connect` → 收帧 `parse`(wire client)→ `Command` 盖 `origin` → `inbox`;每帧 `heartbeat`;退出 `unregister` + 条件 `Disconnect`。
- [ ] `shell/sender.py`:per-connection `outbound` → `ws.send`(明文 JSON `model_dump_json`),严格保序;队列满丢连 + `Disconnect`。
- [ ] `shell/timer.py`:`_action`(room 键)+ `_liveness`(nick 键);`TurnChanged`/`ClearAction` 驱动;`Timeout`/`Cleanup` 投 `inbox`;staleness 由 reduce 兜(`epoch`/`OFFLINE`)。
- [ ] `shell/persist.py` 桩:最小 `WriteBuffer`(内存/日志即可,先不接 DB);P4 再换双缓冲 + PersistWriter + ORM。
- [ ] `shell/lifespan.py` 最小:预置 `ROOMS`、起 GameLoop/Timer、挂 **dev ws 端点**(明文)。
- [ ] 冒烟:前端连 dev 端点 → `SitDown`/`BuyIn`/`SetUserStatus(ready)`/`StartHand`/`PlayerAction` → 看 `HandStarted`/`HoleCards`/`PlayerActed`/`HandShowDown` 广播。
- [ ] `tests/shell/`:GameLoop 工作副本回滚(失败 world 未动)、dispatch 路由、顶替身份判定。

> **明文 dev 端点是临时脚手架**:无鉴权/无加密,仅本机联调用;**绝不上线**。P5 国密信道落地时,Receiver/Sender 的握手与帧编解被 `SecureChannel` 替换,dispatch/GameLoop/reduce 不变(加解密封装在 ws 边界,见 [connection.md](../../connection.md))。

## 增量协议交付(贯穿后续)

每落一个 reduce/shell 模块,**同篇**补它的 wire 切片:JoinRoom+`StateSnapshot`(整桌快照:座位/筹码/button/board/pot/acting/自己底牌——字段随落地定)、`UserJoined`、免盲投票(`OpenFreeEntryVote`/`VoteFreeEntry` + 投票态广播)、`RoomChat`+`ChatMessage`、`Set*Blind` + 配置广播、REST(leaderboard/hands/profile,走 `openapi-typescript`)。每次重 codegen,前端跟随。

## 自 review(本篇 · 纯计划)

- **③ 文档一致**:重排只改 TODO 顺序 + README §4 指针,不动架构/不变量;wire 单一事实源、隐私红线、身份不进报文照 [wire.md](../../wire.md) 不变。明文 dev 端点明确标「临时、dev-only、P5 替换」,不与「连接绑 nick / 加解密在 ws 边界」(connection.md)冲突——只是先放一个无加密实现、后替换。
- **⑦ 账本**:本篇即「讨论(前端解锁)→ 决策(协议+明文端点、加密最后、增量交付、wire 收编 messages.py)→ 落文档」;提交引用 `0016`、全英文。
- **①②④⑤⑥ 不适用**(无代码/数据模型/测试改动)。
- **批判性**:`reduce 直接产 wire DTO`(收编 messages.py)是顺带定的架构选择,依据 models.md/README「core 可 import wire DTO」;若 W 阶段落地时发现 Pydantic 在 core 纯单测里有摩擦(构造成本/可变性),回退为「messages.py 保留 + dispatch 投影」并在该篇记录。

## 待办 / 下一步

- 执行 **W 阶段**(wire 首批协议 + codegen),再 **D 阶段**(明文 dev 端点),让前端联调。
- 后续按重排表推进,每模块补协议切片;**P5 国密信道最后做**。
