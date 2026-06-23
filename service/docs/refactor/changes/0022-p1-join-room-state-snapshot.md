# 0022 · P1 余项:JoinRoom 进房 + Connect 重连恢复 + StateSnapshot 整桌快照

日期:2026-06-23 · 范围:`app/wire/server.py`(+`SeatView`/`StateSnapshot`/`UserJoined`)、`scripts/gen_wire_ts.py`(注册 `RoomStatus` enum + `SeatView` 值对象)、`app/core/reduce.py`(+`_join_room`、重写 `_connect` 重连、+`_state_snapshot`/`_reconnect_status`)、重生成 `wire.gen.ts`、`tests/core/test_join_reconnect.py`(新)、`tests/wire/test_protocol.py`(补 StateSnapshot 隐私样本)、文档(`lobby.md`/`user.md`/`connection.md`/`wire.md`/`wire-protocol-guide.md`/`core.md`/`TODO.md`)。承「P1 余项」前端头号需求。

## 背景 / 为什么

wire-guide §8 列前端头号缺口:「进房 `join_room` + 整桌快照 `state_snapshot`——刷新即对齐 + 重连的关键」。0018 dev shell 用**预置用户绕开** `JoinRoom`(决策 2),`_connect` 是 no-op、`StateSnapshot` 延后。本批落地这条线的 core + 整桌快照报文。

## 关键设计决策(批判性 + 与文档对齐)

1. **本批 = core(`_join_room`/`_connect` 重连/`_state_snapshot`)+ 出站快照 wire(`StateSnapshot`/`SeatView`/`UserJoined`);client `JoinRoom{room}` 报文 + Receiver 读 DB 富化延后**。理由:`JoinRoom(room, uid, loaded)` 的 `uid`/`loaded` 是 **shell 读 DB** 得来(user.md/lobby.md),不进报文、也无法由通用 `to_command` 构造(它没有 DB)——Receiver 须特殊处理(读 DB→构 `JoinRoom` 命令)。而 **dev shell 无真 DB**(persist 是桩),且 dev 用户预置、无大厅入房流;故 client 入房报文 + Receiver DB 富化随**真 DB 集成**(P4 之后)落地。本批交付:① 全可纯单测的 `_join_room` reduce;② **`Connect` 重连 → `StateSnapshot` 端到端可跑(系统命令,无需 client 报文 + dev 可断/重连验证)**;③ 出站 `StateSnapshot` 类型(前端据它渲染桌面 = 头号需求)。

2. **`StateSnapshot` 隐私 = 结构性缺位 + 收件人自有牌字段,无需 `field_serializer`**(沿 0017 路线;修正 wire.md「StateSnapshot 需 field_serializer」的预期)。reduce **逐收件人**构造快照:`your_hole_cards` = 收件人自己的底牌(在手才有,否则 None);在手玩家投影为 `players: tuple[PlayerView]`,而 `PlayerView` **结构上无 hole_cards** ⇒ 他人底牌无从泄露。比 `field_serializer`「内部持牌、按视角隐藏」更简更强。→ **同步改 wire.md**(StateSnapshot 隐私机制 = 结构性 + your_hole_cards,非 field_serializer)。

3. **`StateSnapshot` 形状**(字段以 .py 为准):`room`/`max_seats`/`button_position`/`small_blind`/`big_blind`/`room_status`(新引 `RoomStatus` 到 wire)/`seats: tuple[SeatView,...]`(**仅已占座**,各带 `seat_position`,空座由 `max_seats` 渲染——避开 `tuple[X|None,...]` 的 codegen 数组并集括号坑)/`watchers: tuple[str,...]`(无座 nick)/进行中手牌(`hand_status: HandStatus|None`、`board`、`pot`、`acting_position`、`players: tuple[PlayerView,...]`、`your_hole_cards: tuple[Card,Card]|None`)。`SeatView{seat_position,nickname,status,points,new_here}`:`points` = 该占座者**当前可用筹码**——在手时取其 `Player.points`(锁入后剩余,使 `seats` 始终是「筹码后手」单一源),不在手时取 `Seat.points`;`status` = `users_in_room` 里的 UserStatus(PLAYING/READY/SITTING_OUT/OFFLINE…)。

4. **`Connect` 重连恢复状态 = 从 world 推断,不存「断线前状态」**:`_disconnect` 已用 `OFFLINE` 覆盖原状态(信息丢失)。重连按 world 真相推断——**在进行中手牌(是其 `Player`)→ PLAYING**(座位/筹码 OFFLINE 期间保留,这是重连最重要的态);**有座但不在手 → SITTING_IN**(需重新 ready,安全);**无座 → WATCHING**。三者皆是合法 `OFFLINE→*` 转移。丢 READY/SITTING_OUT 细分是可接受简化(重新 ready 即可)。→ **同步记 user.md/connection.md**。

5. **`ROOM_FULL` 暂不强制(v1,文档化)**:`_join_room` 只校验 `NO_SUCH_ROOM`(房不存在)+ `ALREADY_IN_ROOM`(单房间约束)。「满」的精确语义(满桌不可进?观战上限?)在 ≤20 在线、房极少下非真实约束,且「满桌不可观战」是有损 UX 的武断规则。故 v1 **不限观战、不强制 ROOM_FULL**;座位可用性由 `SitDown` 的 `SEAT_TAKEN` 兜。`ErrorCode.ROOM_FULL` 保留待容量上限引入。→ **同步改 lobby.md**(标 v1 不强制 + 缘由)。

6. **`_connect` 对在线/大厅用户幂等**:nick 不在任何房(纯大厅)→ no-op;在房但非 OFFLINE(预置 WATCHING / 重复 Connect)→ no-op(不重发快照)。**只有 OFFLINE→重连**才恢复 + 发快照。保证 0018 dev 预置 WATCHING 用户的 Connect 仍是 no-op(不破 dev 冒烟)。

## 打算改什么(开工前)

- `app/wire/server.py`:+ `SeatView` + `StateSnapshot` + `UserJoined` + 注册 `SERVER_MESSAGES`。
- `scripts/gen_wire_ts.py`:`_ENUM_ORDER` + `RoomStatus`;`_VALUE_OBJECT_ORDER` + `SeatView`。
- `app/core/reduce.py`:import `JoinRoom`/`UserJoined`/`StateSnapshot`/`SeatView`;reduce match + `JoinRoom` 臂;`_join_room`;重写 `_connect`(重连);`_state_snapshot(room, room_name, for_nick)`;`_reconnect_status(room, nick)`。
- 重生成 `wire.gen.ts`。
- `tests/core/test_join_reconnect.py`(新):进房(装 users + WATCHING + UserJoined + 快照)/ ALREADY_IN_ROOM / NO_SUCH_ROOM / 重连(PLAYING/SITTING_IN/WATCHING 恢复 + 快照带自有牌、不带他人牌)/ 在线幂等 / 大厅 no-op / 快照(无手 seats、在手 board/pot/players/own-cards/隐私)。
- `tests/wire/test_protocol.py`:`_broadcast_samples` + `StateSnapshot`(序列化:有 `your_hole_cards`、无他人 `hole_cards`/`deck`);`UserJoined`。
- 文档:`wire.md`(StateSnapshot 隐私=结构性+your_hole_cards)、`lobby.md`(ROOM_FULL v1 不强制)、`user.md`/`connection.md`(重连推断状态)、`wire-protocol-guide`(§4 state_snapshot/user_joined + §8)、`core.md`、`TODO.md`。

## 实际改了什么

- **`app/wire/server.py`**:+ `SeatView{seat_position,nickname,status,points,new_here}`、`UserJoined{nickname}`、`StateSnapshot{room,max_seats,button_position,small_blind,big_blind,room_status,seats,watchers,hand_status,board,pot,acting_position,players,your_hole_cards}`;注册 `SERVER_MESSAGES`;import `RoomStatus`。
- **`scripts/gen_wire_ts.py`**:`_ENUM_ORDER` + `RoomStatus`;`_VALUE_OBJECT_ORDER` + `SeatView`;import 二者。
- **`app/core/reduce.py`**:reduce `match` + `JoinRoom` 臂;`_join_room`(校验 NO_SUCH_ROOM/ALREADY_IN_ROOM → 装 `UserState`+WATCHING → `UserJoined` 广播 + `StateSnapshot` 私发);重写 `_connect`(大厅 no-op / 在线幂等 / OFFLINE 重连恢复+快照);`_reconnect_status`(按 world 推断 PLAYING/SITTING_IN/WATCHING);`_state_snapshot(room, room_name, *, for_nick)`(逐收件人投影,seats 取在手剩余/座位筹码,your_hole_cards 仅自己);import `JoinRoom`/`UserState`/`SeatView`/`StateSnapshot`/`UserJoined`。
- **`frontend/src/types/wire.gen.ts`**:重生成(+`RoomStatus` enum、`SeatView`/`StateSnapshot`/`UserJoined` 接口、联合新增成员;`your_hole_cards: [Card,Card]|null`、`seats: SeatView[]`、`hand_status: HandStatus|null`)。
- **`tests/core/test_join_reconnect.py`(新)**:9 测试(进房装载+UserJoined+快照 / 局中观战只见公共面 / ALREADY_IN_ROOM / NO_SUCH_ROOM / 重连 PLAYING+自有底牌+他人不泄 / 重连 SITTING_IN / 重连 WATCHING / 在线幂等 / 大厅 no-op)。
- **`tests/wire/test_protocol.py`**:`_broadcast_samples` + `UserJoined`;新 `test_state_snapshot_carries_only_own_cards_not_others_or_deck`(序列化:有 `your_hole_cards`、无 `hole_cards`/`deck`)。
- **文档**:`wire.md`(StateSnapshot 隐私=结构性+your_hole_cards、待定→已落地)、`lobby.md`(ROOM_FULL v1 不强制 + client/DB 延后)、`connection.md`(重连推断状态)、`user.md`(`Connect`→`JoinRoom` 载入模型,自 review 补)、`core.md`(Command 表 JoinRoom + 事件一览进房/重连两行 + 不变量 6 `Connect`→`JoinRoom` 拒别房,对齐 architecture.md:137)、`wire-protocol-guide`(§1 类型表 + §4 state_snapshot/user_joined + §7 三处底牌 + §8)、`TODO`(JoinRoom `[~]` + reduce/tests 状态行 + 计数)。

**偏离计划**:范围与「打算」基本一致。`seats` 用「仅已占座 + max_seats」而非 `tuple[SeatView|None,...]`——既更省,又避开 codegen 对 `数组内并集` 不加括号的坑(`SeatView | null[]` 会误解析);决策 3 已记此选择。**「打算」列了 user.md 但初版漏改**(对抗 review 维度 ② 抓到的 major,见下「自 review」),已补。

### 自 review 后增补(对抗 review 驱动)

对抗式 7 维 review:**privacy_money / reconnect_logic / join_logic 三维 0 存活发现**(隐私逐收件人 + 结构性缺位、重连推断、单房间约束均核实正确);9 条存活均为文档同步 + 测试加固 + 一处 codegen 防御,已全部当场处理:

- **[major ②] user.md 漏同步**:多处仍按旧模型把 `Connect` 当「积分载入者 / 拒别房者」,与 0022 的 `JoinRoom` 载入 + `_connect` 重连矛盾(且与 user.md §生命周期 自身已说的「载入在 JoinRoom」打架)。**修**:行 82/86/99/104 改为 `JoinRoom` 载入 / `JoinRoom` 到别房即拒,`Connect` 标注为「接入大厅/重连、无 room、不重载」。
- **[minor ⑥ 测试] hasattr 隐私断言是结构性恒真式**:`PlayerView`/`StateSnapshot` 结构上无 `hole_cards`,`hasattr` 恒 False、测不出「reduce 发错收件人」。**修**:在局中重连测试加**值级断言**——对 `model_dump` 产物收集所有 `{rank,suit}` 牌,断言对手 Qh/Jc **不出现**、只出现自己 As/Kd + 公共牌;观战进房测同法(只公共牌)。
- **[minor ⑥ 测试] 缺守恒断言**:进房/重连均补 `assert not any(isinstance(e, Persist) for e in ev)`(不动积分/不落库)。
- **[nit ⑥ 测试] 漏分支**:补 `test_reconnect_seated_but_not_in_hand_restores_sitting_in`(局中、有座、非本手 Player → SITTING_IN);局中重连补 `pot/acting_position` 断言。
- **[nit ① codegen] 数组内并集括号坑**:`_ts_type` 的 `tuple[T,...]` 分支对并集元素加括号(`(X | null)[]`),把「靠约定」升级为「靠工具」+ 加单测 `test_array_of_union_is_parenthesized`(字节守门测不出 TS 语义,故单测兜)。本批无字段触发,产物不变。
- **[minor/nit ②③ 文档] wire-protocol-guide**:§4 删死引用「见 §下『快照』」、§7 「两处底牌」→「三处」补 `state_snapshot.your_hole_cards`、§1 类型表补 `RoomStatus`/`SeatView`。

**驱回**:join_logic 维 2 条候选(WATCHING 是否需查转移表 / ROOM_FULL)经反驳——进房是「装入新成员」非状态转移、置 WATCHING 与 `_sit_down` 等一致;ROOM_FULL v1 不强制是决策 5 文档化的有意取舍,非缺陷。

214 全绿;codegen `--check` 干净;core 无 forbidden import。

## 自 review

方法:按 [review.md](../../review.md) 跑**对抗式 7 维 review 工作流**(每维独立审查者 → 每条候选独立反驳者,隐私维要求实际追踪 `_state_snapshot` 数据来源)。结果:**13 候选 / 9 存活 / 4 驳回**;**最高风险面(隐私/重连/进房逻辑)0 存活**——审查者实跑 reduce 重连路径,确认对手底牌不在序列化产物中、`your_hole_cards` 只取 `for_nick` 自己、重连三态转移皆合法、单房间约束正确。9 存活均文档/测试/codegen 加固,已全部修复(见上「自 review 后增补」)。逐维:

- **① 分层/不变量**:`grep app/core` 无 forbidden import;`_join_room`/`_connect`/`_state_snapshot` 纯同步、helper 不 raise、失败 `return [],Err`;只改工作副本(装 `work.users`/改 `users_in_room`),进房/重连**零 Persist**(新增守恒断言);`StateSnapshot`/`SeatView` 字段为快照值,不持域活引用。**核心红线·隐私**:逐收件人 `your_hole_cards`,`players` 用无 `hole_cards` 的 `PlayerView`,`deck` 不入——值级测试钉死他人牌不泄露。
- **② 代码↔文档**:本批唯一漏改的 user.md 已补(major);wire.md/lobby.md/connection.md/core.md/guide 同步,measure 决策(ROOM_FULL 不强制、client/DB 延后、隐私=结构性)均落文档。
- **③ 文档一致**:guide §1/§4/§7/§8 与 wire.gen.ts、wire.md「三处底牌」一致(修了死引用 + 两处→三处);计数 214 同步 TODO。
- **④ 数据模型**:`StateSnapshot` 用「仅已占座 + `max_seats`」避 `tuple[X|None,...]`;`RoomStatus`/`SeatView` 入 codegen 注册;`your_hole_cards: tuple[Card,Card]|None` 表达「在手才有」无歧义。
- **⑤ 规范**:新增 DTO/字段带中文注释;反直觉处(逐收件人隐私、重连推断、ROOM_FULL 不强制)有「为什么」;无魔法数/死代码。
- **⑥ 测试**:10 测试(进房装载/局中观战只见公共面+值级隐私/ALREADY_IN_ROOM/NO_SUCH_ROOM/重连 PLAYING+值级隐私+pot/acting/重连 SITTING_IN(无手 & 局中有座两路)/重连 WATCHING/在线·大厅幂等)+ wire StateSnapshot 序列化隐私 + codegen 并集括号单测;守恒(无 Persist)默认开。214 全绿。
- **⑦ 账本**:打算↔实际差异(user.md 补改 + review 加固)已记本段;TODO `[~]` + 计数同步;提交引用 `0022`、全英文。对抗 review 存活/驱回逐条入账。

> 批判性自评:本批最高风险是「首个携带自有底牌的 Personal 报文」的隐私。实现从一开始就走「逐收件人 + 结构性缺位」(正确,审查实跑确认无泄露),但**初版测试用 `hasattr` 守隐私是恒真式**——这正是 review.md「绿测覆盖想到的、review 覆盖没想到的」:12 测全绿却没真正锁住「发错收件人」,现改为值级断言(对手具体牌面不出现在序列化产物)才是真护栏。

## 待办 / 下一步

- **client `JoinRoom{room}` 报文 + Receiver 读 DB 富化(uid/loaded)→ 构 JoinRoom 命令**:随真 DB 集成(P4 后);dev shell 无 DB,暂以预置用户绕开(0018)。
- 等大盲再入局时机(①.7-10);`SetSmallBlind`/`SetBuyIn`(P8 配置)。
- shell:重连/顶替的 `StateSnapshot` 投递在 dev receiver 已可经 Connect 走通;背压/重连硬化见 TODO。
