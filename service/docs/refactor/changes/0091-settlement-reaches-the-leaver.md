# 0091 · 让离场者看到自己那手是怎么结算的(BUG-10)

日期:2026-08-25 · 性质:**缺陷修复(core 事件产出)**· 触发:[BUGS.md](../BUGS.md) BUG-10。

## 缺陷是什么

玩家点「离开牌桌」时,如果他一走就只剩一人未弃牌,这一手会**立刻结算**。他参与了这个底池(可能投了不少),却**收不到任何一条说明结算结果的消息**:`HandShowDown`(谁拿什么牌赢的)、`HandEnded`(赢取与退还)全都看不到。界面上就是「点了离开 → 回大厅 → 什么都没发生」。

机理:`Broadcast` 的收件人由 dispatch 在 **commit 之后**按 `world.rooms[r].users_in_room` 解析([dispatch.py](../../../app/shell/dispatch.py)),而离场者在**同一条 reduce 里**已被 `_finalize_hand` 末尾的 `_evict` 移出成员表。等到派发时,他已经不在名单上了。

## 登记时的修法有一条走不通,要更正

[BUGS.md](../BUGS.md) 给了两个候选:「结算事件对离场者改用 `Personal` 补发」**或**「调整驱逐与结算广播的先后顺序」。

**后者走不通**:dispatch 不是逐事件按产出时刻解析收件人的,它对**整批**事件都用 commit 之后的那一份成员表。所以在 reduce 里把 `_evict` 挪到广播之后,一点用都没有——两者在同一条 reduce 内,commit 是原子的。这一条要写进变更记录,免得下一个人照着试一遍。

采用前者。

## 先读设计文档(本仓纪律)

- [architecture.md](../../architecture.md) Event A 组:`Broadcast(room, msg)` 整房广播、`Personal(nick, msg)` 私发单个连接,**私发按 nick 全局索引,不需要 room**——所以给已经不在房里的人补发是这套模型天然支持的,不用改事件契约。
- [core.md](../../core.md) 事件顺序契约:结算那一批是 `HandShowDown` → `HandEnded` → 手尾 `UserStatusChanged` → 驱逐 → `ClearAction`。补发不能打乱它。
- [connection.md](../../connection.md):`Personal` 落到连接上;人已回大厅但**连接还在**(离房 ≠ 断线),所以补发真的送得到。

## 打算怎么改

在 `_settle_and_end` 收完全部结算事件之后,对「**本手参与者 ∩ 本手即将被驱逐者**」各补一份 `Personal`,内容是这一批里描述结算结果的两条:`HandShowDown`(如果有摊牌)与 `HandEnded`。

- 判据取 `{p.nickname for p in hand.players} & room.leaving`——只补给真的参与了这个底池、且真的会在本手末尾离场的人。没参与的观战者不补,没离场的照旧走广播。
- **补发排在原广播之后**,不动既有顺序契约;每人各一份,内容与广播完全相同(同一个 msg 对象,冻结 DTO,可安全共享)。
- **不补 `PlayerActed`**:他自己的那条 auto-fold 是他点「离开」的直接结果,他知道;而且它产在更外层(`_acted_events`),要补得把整条链都改。这是**有意划的范围**,写进记录。

## 要动的文件(预期)

- `app/core/reduce.py`(`_settle_and_end` 末尾补发)
- 测试:`tests/core/test_leave_sitout.py`
- 文档:[core.md](../../core.md)(事件产出一览补一行)、[BUGS.md](../BUGS.md)(划掉 BUG-10 并更正「调顺序」那条修法)、[TODO.md](../TODO.md)

协议面不变(没有新消息、没有新字段),codegen 与 [BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 不动。

## 实际改了什么

按计划落地,**没有偏离**。

- **`core/reduce.py`**:`_settle_and_end` 在算完结算事件之后,调新 helper `_settlement_copies_for_departing(events, departing)` 补私发。
  - `departing` 在调 `_finalize_hand` **之前**算好——那个函数末尾会把 `room.leaving` 清空,晚一步就取不到了。
  - helper 从**已经产出的事件里**挑 `HandShowDown`/`HandEnded` 的 msg 复用同一个对象(wire DTO 冻结,共享安全),而不是另编一份:另编就是第二份事实源,广播与私发迟早会说两样话。
  - 补发排在原广播之后,`sorted(departing)` 使产出顺序确定、便于断言。

### 更正了 BUGS 登记里的一条修法

登记给的第二个候选「调整驱逐与结算广播的先后顺序」**走不通**,已在 [BUGS.md](../BUGS.md) 与 [core.md](../../core.md) 两处写明:dispatch 对**整批**事件用的是同一份 commit 之后的成员表,而 commit 是原子的——在 reduce 里挪 `_evict` 的位置,派发时看到的成员表一模一样。不写清楚的话,下一个人会照着试一遍。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **754 passed**(752 → 754) |
| 前端 vitest | 90 passed(未改前端) |
| 浏览器 `npm run test:e2e` | 16 passed |
| 冒烟 | 通过(守恒 1920 → 1920) |
| 后端改完重启 uvicorn 再跑前端各层 | 是 |

**反向变异验证 2 处**:

| 变异 | 变红的 |
|---|---|
| 去掉补发(退回 BUG-10)| `test_departing_participant_gets_personal_settlement` |
| 补给**所有**参与者(不筛离场者)| 上面那条 + `test_no_settlement_copies_when_nobody_leaves`(稳态不许多出私发)|

第二条变异是特意准备的:只写「离场者收到了」这一个方向的断言,「给所有人都补一份」也照样绿,而那会让每手结算凭空多出 N 份重复报文。

## 自 review

按 [review.md](../../review.md) 七维。本批改的是**事件产出**,最高风险面是**隐私**(补发的是摊牌报文)与**顺序契约**。

- **① 分层 / 不变量**:core 仍纯同步;补发走的是既有的 `Personal` 通道,没有新增第四种对外副作用(architecture.md 对 A 组是封闭集合的约束不破)。helper 是纯函数,只读事件列表,不 raise、不改 world。
- **② 代码↔文档同步**:[core.md](../../core.md) 事件产出一览的「结束」行补上这几份 `Personal`,并在表下写清由来 + 那条走不通的备选修法。协议面零改动,故 codegen / BACKEND_GUIDE 不动(如实核过:没有新消息、没有新字段)。
- **③ 文档↔文档一致**:[BUGS.md](../BUGS.md) 划掉 BUG-10、进「已修复」表,并**更正**登记的第二条修法;[TODO.md](../TODO.md) 无对应待办项(BUG-10 只在缺陷册里)。
- **④ 数据模型正确性**:不新增类型;`departing` 是集合,`&` 保证只覆盖「真的参与了这个底池**且**真的会离场」的人——观战者、旁观的离场者、留下的参与者都不在内。
- **⑤ 规范合规**:helper 带中文注释,讲的是「为什么必须补」与「为什么挪顺序没用」这两处反直觉点;无魔法数、无死代码。
- **⑥ 测试充分**:2 处反向变异确认。**隐私专门核过**:补发的 `HandShowDown` 收件人是**本手的参与者**——他本来就在摊牌广播的合法收件人集合里(未弃牌者的底牌在摊牌时对全房公开,见 core.md 不变量 3),补发没有扩大任何暴露面;`tests/wire/test_protocol.py` 的隐私断言照常通过。**缺口如实记**:(a) 没有端到端(浏览器/冒烟)用例覆盖「离桌触发终局结算」这条路径——现有冒烟的离桌都发生在两手之间;(b) 「多人同手离场」时每人各一份,顺序确定但没有单独用例钉住多人的情形。
- **⑦ 流程账本**:本篇即账本,开工前写「打算」(含对登记修法的证伪)、收工回填,无偏离。

### 顺带发现,未在本批处理

- **`Broadcast` 的收件人解析时机是个更普遍的隐患**:任何「在同一条 reduce 里把人移出房间」的路径,都会让这个人错过同批的所有广播。目前只有手尾驱逐这一处会这么做(离场、清理),而清理路径的当事人本来就已经断线、收不到。要不要把「收件人在 reduce 里定死」做成通用机制(例如给 `Broadcast` 加一个显式收件人集合),是一次事件契约层面的改动,值得单独议——记档,不在本批。
