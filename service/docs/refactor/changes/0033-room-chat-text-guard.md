# 0033 · 房聊 shell 文本防护 + 令牌桶限速

日期:2026-06-24 · 范围:`app/core/errors.py`(+`MESSAGE_TOO_LONG`/`RATE_LIMITED`)、`app/shell/ratelimit.py`(新:`TokenBucket`)、`app/shell/connection.py`(`Connection` +`chat_bucket`)、`app/shell/receiver.py`(`RoomChat` 帧进 reduce 前过文本防护 + 限速)、`app/gameconfig.py`(`ROOM_CHAT_MAX_TEXT_LEN` + 令牌桶参数)、重生成 `frontend/src/types/wire.gen.ts`(ErrorCode 加两码)、`tests/shell/test_ratelimit.py`(新)+ `tests/shell/test_receiver.py`(房聊防护)、文档(`messaging.md`/`error.md`/`wire-protocol-guide.md`/`TODO.md`)。兑现 [0021](0021-p1-room-chat.md) 显式延后的「房聊 shell 文本防护 + 限速」(commit 3610df4 记入 TODO)。

## 背景 / 为什么

[0021](0021-p1-room-chat.md) 把房聊文本校验**从 reduce 移到 shell**(core 只认「在不在房」这一游戏判据、保持只读零配置),但 shell 侧防护**延后未落**——当前不可信客户端可发**空 / 超长 / 刷屏**的 `room_chat` 直达 GameLoop + 广播全房。[messaging.md](../../messaging.md):23 + 契约 4 定:在 **Receiver** 收到 `RoomChat` 先做「非空 + `text ≤ ROOM_CHAT_MAX_TEXT_LEN`」+ 过**令牌桶**(发件人维度),超了直接丢 + 回 `Err`,**不让刷屏 / 超长文本占 GameLoop**;阈值进配置。

## 关键设计决策(批判性,对齐 messaging.md)

1. **防护在 Receiver、进 reduce 之前**(messaging.md 契约 4):与 `JoinRoom` 读 DB 富化同一拦截点(`_frame_to_command`)。`_room_chat` reduce 保持只读、不重复校验文本(0021 决策)。失败 → 构 `ErrorMessage` 投本连接 `outbound`(同解析错误回发路径),`return None` 不进 `inbox`。
2. **校验序 = 内容(空/长)先,令牌桶后**:内容非法的帧在 Receiver 即被拒、**根本不到 GameLoop**(不占 GameLoop),故无需为它耗令牌;令牌桶只对**内容合法**的帧计数——它防的是「合法消息刷屏占 GameLoop / 广播」。空帧/超长帧的洪泛由 Receiver 廉价拒 + 其 Err 回执灌满发起方 `outbound` 触发背压丢连兜(0031)。
3. **令牌桶挂 `Connection`(每连接),非全局 nick 表**:模型 2 下 nick↔连接 1:1,每连接桶 ≈ 发件人维度;桶随连接生死、无需清理。**简化残留**:重连/顶替起新连接 → 桶重置(重连者可借此重置限速)——重连有握手开销 + ≤20 内网,v1 接受;要严格按 nick 持久可后续改全局表(同 Timer liveness)。
4. **两个新 `ErrorCode`**:`MESSAGE_TOO_LONG`(超长)、`RATE_LIMITED`(限速);空文本(strip 后为空)复用 `INVALID_MESSAGE`(与 Receiver 解析层「字段不合法」同义,不另开 `EMPTY_MESSAGE`)。`ErrorCode` 进 wire codegen(`_ENUM_ORDER`)→ **重生成 `wire.gen.ts`**。
5. **令牌桶用单调时钟**(`time.monotonic`,同 [timer.md](../../timer.md)):`try_consume(now, cost)` 先按 elapsed 补令牌(不超 capacity)再扣;纯计算、可注入 now 单测。广播**原文**(只拒不改用户内容;空判 strip 仅作判据,长度判原文 = 即将广播/未来落历史的串)。

## 打算改什么(开工前)

- `app/core/errors.py`:`ErrorCode` +`MESSAGE_TOO_LONG`/`RATE_LIMITED`(行内注释)。
- `app/shell/ratelimit.py`(新):`TokenBucket{capacity, refill_per_sec, tokens, updated_at}` + `create(...)` + `try_consume(now, cost=1.0)`。
- `app/shell/connection.py`:`Connection` +`chat_bucket: TokenBucket`(`create` 时按 gameconfig 建)。
- `app/shell/receiver.py`:`_frame_to_command` 拦 `RoomChat` → `_guard_room_chat`(空→INVALID_MESSAGE / 超长→MESSAGE_TOO_LONG / 限速→RATE_LIMITED;回 Err 投 outbound、return None;过则构 `commands.RoomChat`)。
- `app/gameconfig.py`:`ROOM_CHAT_MAX_TEXT_LEN`/`ROOM_CHAT_RATE_BURST`/`ROOM_CHAT_RATE_PER_SEC`(dev 常量)。
- 重生成 `frontend/src/types/wire.gen.ts`(`python scripts/gen_wire_ts.py`)。
- `tests/shell/test_ratelimit.py`(新):补令牌/不超容量/突发耗尽/稳态恢复/cost。
- `tests/shell/test_receiver.py`:房聊正常过 / 空拒 / 超长拒 / 限速拒(预耗桶)+ 各回对应 code。
- 文档:`messaging.md`(防护落地)、`error.md`(两码)、`wire-protocol-guide.md`(房聊错误码)、`TODO.md`。

## 实际改了什么

- **`app/core/errors.py`**:`ErrorCode` +`MESSAGE_TOO_LONG`/`RATE_LIMITED`(行内注释);`INVALID_MESSAGE` 注释补「含房聊空文本」。
- **`app/shell/ratelimit.py`(新)**:`TokenBucket{capacity, refill_per_sec, tokens, updated_at}` + `create(capacity, refill_per_sec, now)`(满桶)+ `try_consume(now, cost=1.0)`(先按 `max(0, elapsed)` 补、封顶 capacity,再扣;倒退钟不生令牌)。纯计算、注入 now。
- **`app/shell/connection.py`**:`Connection` +`chat_bucket: TokenBucket | None`;`create` 按 `gameconfig.ROOM_CHAT_RATE_BURST`/`_PER_SEC` + `time.monotonic()` 建满桶。
- **`app/shell/receiver.py`**:`_frame_to_command` 拦 `wire_client.RoomChat` → `_guard_room_chat`(空 strip→`INVALID_MESSAGE` / 超长→`MESSAGE_TOO_LONG` / 限速→`RATE_LIMITED`,回 `ErrorMessage` 投 `outbound`、`return None`;过则构 `RoomChat(origin=conn.nick, text=原文)`)。import `time`、core `RoomChat`。
- **`app/gameconfig.py`**:`ROOM_CHAT_MAX_TEXT_LEN=500`/`ROOM_CHAT_RATE_BURST=5.0`/`ROOM_CHAT_RATE_PER_SEC=1.0`(dev 常量 + 单位/语义注释)。
- **`frontend/src/types/wire.gen.ts`**:重生成(`ErrorCode` 联合 +`MESSAGE_TOO_LONG`/`RATE_LIMITED`)。
- **`tests/shell/test_ratelimit.py`(新,6 测)**:满桶起、突发耗尽、稳态 1/s 恢复、补充封顶、倒退钟不生令牌、`cost>1`。
- **`tests/shell/test_room_chat_guard.py`(新,7 测)**:正常过(身份盖连接 nick)、空拒 `INVALID_MESSAGE`、超长拒 `MESSAGE_TOO_LONG`、`==MAX` 边界过、空桶限速 `RATE_LIMITED`(基准设未来 → elapsed 恒 0 确定性)、内容非法不耗令牌、端到端突发后限速。
- **文档**:`messaging.md`(防护落地 + 校验序 + 桶挂连接残留)、`error.md`(+文本/滥用防护行 + 示意码补两码)、`wire-protocol-guide.md`(`room_chat` 三种 error 码)、`TODO.md`(P7 房聊防护划掉)。

303 全绿(+13:ratelimit 6 + room_chat_guard 7);codegen `--check` 干净(重生成已对齐)。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 5 维 review **子代理工作流**(令牌桶正确性 / 守护逻辑+身份 / 分层+codegen / 测试充分 / 文档同步;各维 1 审查者 → 每候选 1 反驳者)。结论 **go(唯一 must-fix = 本「自 review」段当时未填,纯流程门槛,现已补)**:22 候选全部驳到 nit,**三大高风险面均判 sound**。逐维:

- **① 分层 / 不变量**:`ratelimit.py` 纯计算(无 core/fastapi/sqlalchemy import);`connection.py` import ratelimit 合分层;`ErrorCode` 加两 shell-only 码进 **core 共享错误词汇表**(Receiver 早已用 core `INVALID_MESSAGE`,一致)→ 重生成 `wire.gen.ts`、codegen `--check` 干净。`_room_chat` reduce **保持只读、不重复校验文本**(0021/0033 设计:shell 是唯一文本门)。
- **② 代码↔文档**:防护落地同步 messaging.md(校验序 + 桶挂连接残留)、error.md(+文本/滥用防护行 + 示意码补两码)、wire-guide(`room_chat` 三码);wire `to_command` 的 `RoomChat` 分支补注释说明实路径走 `_guard_room_chat`(保留作通用映射 + 协议直测,不删/不 raise)。
- **③ 文档↔文档**:wire-guide 错误码改 UPPER_CASE 对齐源枚举(顺手修 `join_room` 行既存小写漂移);TODO P7 房聊防护划掉;changes 交叉链一致。
- **④ 数据模型**:`TokenBucket` 字段带语义注释;`chat_bucket: TokenBucket | None`(默认 None 适配 dataclass 字段序 + 守护 `is None` 兜底,实际 `create()` 必建)。
- **⑤ 规范**:Err.detail 中文(与既有 reduce/receiver Err detail 一致,前端按 code 渲染);`gameconfig` 常量带单位/语义注释;无裸字面量(守护引 `gameconfig.*`)。
- **⑥ 测试**:**最高风险面实证**——(1) 无非法/超长文本到 reduce:守护是 `to_command` 前唯一门、空/超长 `return None` 投 Err 到 outbound 不进 inbox(sync 单测 + **async 端到端** valid/空/超长三路穿 run_receiver);(2) 身份:`RoomChat(origin=conn.nick)` 不信报文;(3) 限速:内容先于耗令牌(`test_invalid_content_does_not_consume_token`)、`try_consume` 倒退钟 `max(0,elapsed)` 不生令牌、封顶 capacity。采纳 3 条 nit:突发测**精确 `== BURST`**(杜绝 5-vs-6 off-by-one)、补 **首尾空白原样广播**测(钉死 strip-判但发原文)、补 async 超长穿管线测。
- **⑦ 账本**:打算↔实际 + 采纳 nit 已记;TODO 划项 + 计数 307;提交引用 `0033`、全英文。

**对抗核实存活 / 驳回**:functional 候选**全部驳到 nit**(无 blocker/major)。*驳回的关键候选*:「重连重置桶 = 滥用洞」——驳回:决策 3 明记为 v1 有意取舍(握手开销 + ≤20 内网兜),非缺陷;「`is None` 死分支」——保留作防御(restructure dataclass 字段序得不偿失)。*采纳的 nit(4,均已修)*:精确 burst 断言、首尾空白原样、async 超长穿管线、wire-guide UPPER_CASE 码 + client `RoomChat` 注释。*唯一 must-fix*:本段当时是占位符(review.md 硬门槛「无自 review 段不 push」)——现已据本次 review 据实回填,提交前满足。

> 批判性自评:本批安全要点是「不可信文本不得直达 broadcast/GameLoop」,而真正的护栏是**守护处于 `to_command` 之前的唯一拦截点 + reduce 只读不兜底**——review 实跑确认无旁路。次要点是 strip 语义:**strip 仅作空判、广播原文**,review 指出「若误写 `text=msg.text.strip()` 全 307 测仍绿」,故补值级「首尾空白原样」测才是真护栏(再现 review.md「绿测覆盖想到的、review 覆盖没想到的」)。

## 待办 / 下一步

- 房聊**环形缓冲**(每房最近 N 条)+ `FetchRoomChat`(messaging.md §持久化)随后续。
- 私聊 DM 未读收件箱(messaging.md §私信)。
- 令牌桶参数(P8)随 `gameconfig` env 化;严格按-nick 限速(全局表)如有需要再上。
