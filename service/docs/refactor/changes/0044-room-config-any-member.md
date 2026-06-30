# 0044 · 房间参数配置放开授权:任何在房成员可改(去房主)

日期:2026-06-30 · 范围:`app/core/reduce.py`(`_room_config_guards` 去 seat-0 授权)、`app/core/errors.py`(删 `NOT_ROOM_OWNER`)、`frontend/src/types/wire.gen.ts`(重生成:ErrorCode 去该成员)、`tests/core/test_room_config.py`(改授权臂)、`docs/lobby.md`/`core.md`/`wire-protocol-guide.md`/`TODO.md`。承接 0043,落地用户设计决策。

## 背景 / 讨论(设计决策,记入账本)

**问题**:0043 给 `SetSmallBlind`/`SetBuyIn` 加的授权判的是「你是不是此刻 0 号位占座者」(`NOT_ROOM_OWNER`)——一个**位置式、会漂移的准房主**,是整个房间里唯一带「准权限」的点(开局/投票/聊天都是 peer 无房主)。lobby.md 当时标了「残留简化:房主身份随坐席流动,持久 owner 待 CreateRoom」。

**讨论结论(用户定)**:**不需要房管理(无踢人/关房/房主),每个人都能改配置(如买入)**。即:
- 去掉「房主/0 号位」这一准权限,**任何在房成员**(在 `users_in_room` 内,含观战者)都能改房间参数。
- 不引入持久 owner(`CreateRoom` 也不做房主)。

**为何这是对的(本项目约束:内网、在线 ≤20、积分非货币、预置房不销毁)**:房间本就是「运营预置的基础设施、非玩家私产」,peer/无房主是其底色;0043 的 0 号位准权限是光谱里最尴尬的中点(既有集权味、又有身份漂移的意外语义)。去掉它让房配回归 peer 模型,与开局/投票一致,并省掉 owner 生命周期(继承/转让/掉线/空房销毁)——这些在「不销毁的预置房」里不成比例。详见与本批同期的设计讨论。

## 关键设计决策

1. **授权降为「在房即可」**:`_room_config_guards` 去掉 `room.seats[0]` 占座者校验与 `NOT_ROOM_OWNER`。留两道**非授权**门:
   - `NOT_IN_ROOM`:不在本房不能改(这是命令路由的必然,不是「权限」)。含观战者——观战者在 `users_in_room` 内,故可改(用户明示「每个人」)。
   - `HAND_IN_PROGRESS`:**correctness 门,非授权**——改 `small_blind` 会污染已在 StartHand 锁入本手的下盲/大盲派生,故仍仅两手之间可改(两个命令对称,`buy_in` 虽 core 不读也一并 gate,保持简单一致)。
2. **删 `NOT_ROOM_OWNER` 错误码**(不留死代码,coding_principle):0043 新增、仅此一处用,放开后无消费方 → 删。`ErrorCode` 进 wire(`ErrorMessage.code`),故 `wire.gen.ts` 的 `ErrorCode` 联合去该成员、重 codegen。`INVALID_SMALL_BLIND`/`INVALID_BUY_IN` 保留(≤0 + shell 上下限仍用)。
3. **无协议形状变化**:`set_small_blind`/`set_buy_in`/`RoomConfigChanged`/`StateSnapshot.buy_in` 报文不变;只是授权逻辑与一个错误码删除。shell `_guard_room_config`(上下限)不变。

## 打算改什么(开工前)

- `reduce.py`:`_room_config_guards` 删 seat-0 块 + 更新本节头注释(授权=任何在房成员)。
- `errors.py`:删 `NOT_ROOM_OWNER`。
- `wire.gen.ts`:重生成。
- `tests/core/test_room_config.py`:删「非 0 号位/观战者/空 0 号位/局中先吃 NOT_ROOM_OWNER」拒臂;加「非庄/非 0 号位座位成员可改、观战者可改、坐出成员可改、空 0 号位不妨碍」放行臂;局中改判为 `HAND_IN_PROGRESS`(任何成员)。
- 文档:lobby.md(改授权段)、core.md(命令表行)、wire-protocol-guide.md(行去 NOT_ROOM_OWNER、改「任何在房成员」)、TODO.md(0043 行残留注 → 指向 0044)。

## 实际改了什么

- **`app/core/reduce.py`**:`_room_config_guards` 删 seat-0 块(`seat0 = room.seats[0]` + `NOT_ROOM_OWNER` 返回),现仅 `NOT_IN_ROOM` → `HAND_IN_PROGRESS` 两道(在房 → 非局中);本节头注释改「授权 = 任何在房成员」+ 标注 `HAND_IN_PROGRESS` 为 correctness 门非授权。`_set_small_blind`/`_set_buy_in`/`_room_config_changed` 主体不变。
- **`app/core/errors.py`**:删 `NOT_ROOM_OWNER`(放开后无消费方,不留死代码)。
- **`frontend/src/types/wire.gen.ts`**:重生成 —— `ErrorCode` 联合去 `NOT_ROOM_OWNER`;无其它变化(报文形状不变)。
- **`tests/core/test_room_config.py`**:重写授权臂(17→17,净 0:删 owner-拒臂、加等量 member-放行臂)。删:非 0 号位/观战者/空 0 号位拒臂、「非 owner 局中先吃 NOT_ROOM_OWNER」gate-order 测、SetBuyIn 非 owner 拒。加放开臂(每条在 0043 都会 `NOT_ROOM_OWNER`):**非 0 号位在座成员(B@座1)/ 观战者 / 0 号位空 / 坐出非 0 号位成员 可改 SetSmallBlind;观战者 / 非 0 号位成员 可改 SetBuyIn**;局中改判为 `HAND_IN_PROGRESS`(任何成员);保留 NOT_IN_ROOM / ≤0 / 免盲投票双向 / StateSnapshot.buy_in / 守恒(seats/hand/users 三快照)。
- **文档**:lobby.md(§房间参数配置 改「任何在房成员、无房主」+ correctness 门说明)、core.md(命令表行)、wire-protocol-guide.md(client 两行去 NOT_ROOM_OWNER、改「任何在房成员」)、TODO.md(0043 行授权注 → 0044 去房主)。**0043 记录不改**(历史属实)。
- **无协议形状 / 无 shell 改动**:`set_small_blind`/`set_buy_in`/`RoomConfigChanged`/`StateSnapshot.buy_in` 报文不变;shell `_guard_room_config`(上下限)不变。

419 全绿(419→419,净 0:删拒臂 = 加放行臂);codegen `--check` 干净;core 无越层 import;无残留 `NOT_ROOM_OWNER` 实引用(仅一条 test 注释讲历史)。

## 自 review

方法:对照 [review.md](../../review.md) 跑 **3 维 compact 对抗 review 子代理工作流**(core/门正确性 · 死代码/codegen/wire · 测试/文档一致;每代理对候选**自反驳**后才报)。**3 agent、5 确认(0 真 code bug)**:core 维 + codegen 维 `findings:[]`(seat-0 删除干净、`HAND_IN_PROGRESS`/`NOT_IN_ROOM` 门未弱化、纯度保持、`NOT_ROOM_OWNER` 实引用清零、`ErrorCode` 24 成员对齐、报文形状不变,均经 agent 实读 + 跑 pytest/`--check` 确认)。5 确认全在测试/文档维,逐条采纳:

- **① 分层 / 门正确性**:授权降为「在房即可」,但 **`HAND_IN_PROGRESS` correctness 门保留**(任何成员局中改盲仍拒,test_*_mid_hand 钉);`NOT_IN_ROOM` 仍挡非成员。agent 追 `_set_small_blind`/`_set_buy_in`:让非就座观战者改配置**无**状态假设破坏(房配只读 `room.small_blind`/`buy_in`、不碰座位/手牌)——无新 correctness 洞。core 纯(grep 无 gameconfig import)。
- **② 死代码 / codegen / wire**:`NOT_ROOM_OWNER` 实引用清零(仅 test 注释讲历史)、`ErrorCode` 24 成员与 `wire.gen.ts` 一致、`--check` 干净、报文形状不变——agent 跑脚本确认,`findings:[]`。
- **②③ 代码↔文档**:lobby/core.md/wire-guide client 行已改「任何在房成员」。**抓到 2 处漏改**(wire-guide `room_config_changed` 服务行 :71 + 「已交付」:118 仍写「仅 0 号位占座者」)——已补改。TODO :33 reduce 行 0043 注「授权占座 0 号位」→ 补「0044 放开」。
- **⑥ 测试**:**抓到 1 false-green**(高价值)——坐出测原用 A@座0,而 A 在 0043 本就是 0 号位占座者、授权也过,故**测不到放开**(agent 用「重插 owner-check 变异」实证:该测仍 PASS,而四条真放开臂 FAIL)。改用 B@座1 SITTING_OUT(0043 会 NOT_ROOM_OWNER)杀变异;另补 SetBuyIn 非 0 号位成员臂(对称)。
- **⑦ 账本**:回填本段 + 实际改动;test_room_config 17(内容重写)、suite 419 不变;提交引用 0044、全英文。

**对抗核实存活 / 采纳 / 驳回**:5 候选全 `survives_refutation=true`,全采纳(2 真 doc 漏改、1 test false-green、2 nit 补全)。0 真 bug,但 review 兑现「绿测 ≠ 可提交」——它用变异实证抓出「名字像测放开、实则被 0043 也放行」的伪覆盖,以及两处我漏改的当前行为文档。修后复跑 419 全绿。

## 待办 / 下一步

- 配置改动是 peer 行为,无 owner;若日后真要房管理(踢人/关房),再单独议(用户当前明示不需要)。
- `app/config.py`(基础设施 `DATABASE_URL`)仍是 0042/0043 起的余项。
