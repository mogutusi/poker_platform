# 0084 · 给 `new_here` 一条传达渠道(0082·A)

日期:2026-08-21 · 性质:**协议补齐(后端 wire + core 事件)+ 前端消费**· 触发:[TODO.md](../TODO.md)「功能与验证」第一项,即 [0082](0082-vote-config-and-hand-end-status.md) §四自己点名的下一步。

## 问题(0082 记下的原话)

`new_here` 只出现在 `StateSnapshot` 的 `SeatView` 里,而服务端在 `_start_hand` 末尾重标 `new_here` 时**不发任何携带它的事件**,所以客户端那份打完一手就过期。

这是 [0082](0082-vote-config-and-hand-end-status.md) §三修掉的「打不了第二手」的同一类病:**服务端改了状态,却没有任何事件承载这个改动**。0082 当时说它「后果轻得多——只影响一个按钮的显隐,不卡住玩法」,所以留到本批。

后果比记的还多一条:前端为了让「观战→入座」能显示,在 `user_status_changed` 新建座位时**硬写 `new_here: true`**([room.ts](../../../../frontend/src/store/room.ts))。那是前端在替服务器裁定规则,破前端不变量 1(「不复算规则」)——虽然当前恰好猜对(`_sit_down` 建的 `Seat` 确实 `new_here=True`),但它是**猜**,不是服务器说的。

## 先读设计文档(本仓纪律)

[rules.md](../../rules.md) ①「入局与防躲盲」+「免盲投票」:

- `new_here` 落的是「上一手是否参与」,只在两处变:`_sit_down` 新建座位(`True`),`_start_hand` 末尾——被发牌者清 `False`、未被发牌的在座者一律重标 `True`。
- 它是**公开信息**:早就整份放在 `StateSnapshot.SeatView.new_here` 里发给房内所有人(含观战者),所以补事件不涉及任何隐私面。
- 投票候选 = 当前 `new_here` 座位;投票人 = 非 `new_here` 且 `READY_TO_PLAY` 的座位。

## 打算怎么改

0082 给了两个候选:「让 `UserStatusChanged` 带 `new_here`」或「重标后补广播」。查下来**这两个是同一件事**,合起来做一次:

1. **`UserStatusChanged` 加 `new_here: bool | None`**(`None` = 未就座,与既有 `seat_position: int | None` 同一语义)。这样每一条座位事件都由服务器如实说明该座位欠不欠入局费,前端那句硬写的 `new_here: true` 当场删掉。
2. **`_start_hand` 末尾重标之后,对 `new_here` 值真的变了的座位各产一条 `Broadcast(UserStatusChanged)`**,排在 `HandStarted` + `HoleCards` 之后(与 [0082](0082-vote-config-and-hand-end-status.md) 手尾广播同款次序:先知道这手怎么开的,再知道各座位落到什么状态),born-all-in 跑公共牌之前。
   - 只发**真的变了**的座位:稳态牌桌上每手 0 条,新人入局或有人坐出时 1–2 条,不是每手刷一屏。

3. **前端**:`room.ts` 改用 `msg.new_here`;牌桌上把欠入局费的座位标出来;免盲投票面板显示当前候选。
   - **不改「开票按钮一直可点、由服务器裁决」这个决定。** 候选集现在能准确知道了,但「有没有合格投票人」仍是规则(非 `new_here` 且 `READY_TO_PLAY`),前端照旧不算——0082 定的「不预判自己不可能算准的东西」继续成立,能做的只是把服务器给的候选**如实显示**出来,让人知道值不值得点。

## 要动的文件(预期)

- `app/wire/server.py`(`UserStatusChanged` 加字段)、`app/core/reduce.py`(5 处构造点 + `_start_hand` 末尾新广播)
- `scripts/gen_wire_ts.py` 产物 `frontend/src/types/wire.gen.ts` 重生成
- 前端 `src/store/room.ts`、`src/components/FreeEntryVote.tsx`、牌桌页座位渲染
- 文档:[core.md](../../core.md)(事件清单)、[rules.md](../../rules.md) ①(补「怎么传达」)、[wire-protocol-guide.md](../../wire-protocol-guide.md)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(协议形状变化,0070 起的用户指示)

## 实际改了什么

按计划落地,**没有偏离**。`UserStatusChanged` 加字段 + `_start_hand` 末尾补广播,查下来 0082 给的两个候选确实是同一件事。

### 后端

- **`wire/server.py`**:`UserStatusChanged` 加 `new_here: bool | None`。**必填、不给默认值**——这样 5 处产出点漏填就编译不过(实测:加完先跑测试,10 个用例立刻红,正是它们各自漏填)。语义与既有 `seat_position: int | None` 对齐:未就座为 `None`。
- **`core/reduce.py`**:抽 `_new_here_of(room, nick)`(取该 nick 座位的 `new_here`,无座为 `None`),5 处产出点各自如实填:`_sit_down` / `_disconnect` / `_connect` 重连臂 / `_set_user_status`(起身后座位已腾空 ⇒ 两个字段一起为 `None`)/ `_finalize_hand` 的手尾状态广播。
  - 手尾那处的 `new_here` **在循环里就地取**,不留到最后:本手离桌者的驱逐就在同一函数末尾,那之后座位已经没了,再取就取不到。
- **`_start_hand`**:发牌前先记 `new_here_before` 快照,末尾重标之后对**值真的变了**的座位各产一条 `Broadcast(UserStatusChanged)`,排在 `HandStarted` + `HoleCards` 之后、born-all-in 跑公共牌之前。座位在册但 `users_in_room` 里没这人时跳过(内部不一致的防御臂,同 `_room_chat`),不让开局因 KeyError 崩掉。
- 重跑 codegen → `wire.gen.ts` 的 `UserStatusChanged` 多出 `new_here: boolean | null`。

### 前端

- **`store/room.ts`**:删掉新建座位时硬写的 `new_here: true`,改用 `msg.new_here`;而且**在座内变状态时也更新它**——原来那条 `map` 只改 `status`,即便服务器说了也会被忽略。
- **`app/game/page.tsx`**:座位派生出 `owesEntry`(值来自服务器),座位上挂一个「等入局」小标(`data-owes-entry`);开票按钮带上「N 人等入局」并在 title 里列出是谁。
- **开票按钮「一直可点、由服务器裁决」的决定没有改**,只把注释里已经过时的那半理由改掉:0084 之后 `new_here` 可靠了,所以「有没有候选」能如实显示;但「有没有合格投票人」(非 `new_here` 且 `READY_TO_PLAY`)仍是规则,前端照旧不算——算它就是复算服务器规则。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **745 passed**(737 → 745,新增 8) |
| 前端 vitest | **82 passed**(81 → 82) |
| 浏览器 `npm run test:e2e` | 12 passed(vote-config 补了 3 条断言) |
| 协议冒烟 / 残留自愈冒烟 | 均通过(守恒 1800 → 1800) |

**反向变异验证 5 处**,每处都确认「改回旧行为 → 对应测试变红」:

| 变异 | 变红的测试 |
|---|---|
| 去掉 `_start_hand` 的补广播 | core 3 条 |
| 广播全部座位(不判「真的变了」) | core 2 条(含「稳态一条都不发」) |
| `_set_user_status` 硬写 `new_here=True` 而不是读 world | core 1 条(起身应报 `None`) |
| 前端改回硬写 `new_here: true` | vitest 1 条 |
| **去掉补广播 + 重启 uvicorn 后跑浏览器** | `[data-owes-entry]` 期望 0 实得 2 —— 开局后标志确实一直挂着不掉,缺陷在真浏览器里复现 |

最后一条是本批最有价值的验证:它证明这个缺口**在真界面上看得见**,而不只是协议层的洁癖。

## 自 review

按 [review.md](../../review.md) 七维。本批改的是协议形状 + core 事件产出,最高风险面是**事件产出的完备性**(漏发 = 客户端静默过期,正是本批要修的病)与**隐私**(新字段会不会泄露不该给的东西)。

- **① 分层 / 不变量**:core 仍纯同步、零 IO;新广播在 reduce 里产出、由 dispatch 路由,没有绕过事件机制;`_new_here_of` 是 core 内的纯读 helper,不 raise。前端不变量 1(不复算规则)这次是**被修复的一方**——硬写 `new_here: true` 正是违反它,现在删掉了。
- **② 代码↔文档同步**:[core.md](../../core.md) 事件表的「开局」行补上这条广播,并在表下写清由来与次序;[rules.md](../../rules.md) ① 的实现细节段补「重标要广播」;[wire-protocol-guide.md](../../wire-protocol-guide.md) 的消息目录补字段;[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 按 0070 起的用户指示同步(协议形状变了)。
- **③ 文档↔文档一致**:本篇链回 0082;前端 `game/page.tsx` 里那段解释「为什么不预判」的注释**已连同理由一起更新**——它引用的事实(「前端无法可靠知道 new_here」)被本批推翻了一半,留着就是新的反事实。`e2e/vote-config.spec.ts` 里同款的陈旧注释也一并改了。
- **④ 数据模型正确性**:`bool | None` 与 `seat_position: int | None` 同语义、同时为 `None`,不引入「无座却报 new_here」这种不可能态;字段**必填**,漏填是编译期错误而不是静默 `None`。
- **⑤ 规范合规**:新字段带中文含义注释并指向 rules.md ①;无魔法数;无死代码;注释讲「为什么」(为什么只发变了的、为什么手尾要就地取、为什么开票入口的决定不变)。
- **⑥ 测试充分**:8 条 core 测(含「稳态一条都不发」这个防刷屏判据、事件次序、四处产出点各自的取值)+ 1 条 vitest + 3 条浏览器断言,全部做了反向变异验证。**隐私专门核过**:`new_here` 早已整份在 `StateSnapshot.SeatView` 里发给房内所有人(含观战者),本批只是让同一个公开事实走事件通道,没有扩大任何暴露面;`tests/wire/test_protocol.py` 的「广播不含底牌/牌堆」断言照常通过。**缺口如实记**:(a) born-all-in 那条路径上「补广播排在 runout 之前」只有代码次序保证,没有单独用例(构造 born-all-in 需要特定筹码配比);(b) 三人以上、有人中途坐出再回来的多手连续场景,只在 core 层测了单手,没有端到端多手验证。
- **⑦ 流程账本**:本篇即账本,开工前写「打算」、收工回填「实际」。与打算相比无偏离,如实记下这一点(不是每批都会有偏离)。

### 顺带发现,未在本批处理

- `room.ts` 的「在座内变状态」分支原本**只改 `status`**,连服务器已经给的其它字段也不会更新。本批顺手让它也更新 `new_here`,但同一个分支对 `points` 仍然不更新(靠 `player_bought_in` 单独维护)。目前没有已知错处,记档留意。
- **BUG-9**(重连/顶替后免盲投票面板消失,`StateSnapshot` 不投影 `entry_vote`)与本批是同一类病、同一块界面,但它在 [BUGS.md](../BUGS.md) 的缺陷册里、不在本次「功能与验证」的范围内,按用户指定的顺序留到缺陷批次。
