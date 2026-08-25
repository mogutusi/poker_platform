# 0088 · 把加注下限与免盲投票投影上 wire(BUG-19 + BUG-9)

日期:2026-08-24 · 性质:**协议补齐(后端 wire + core 投影)+ 前端消费**· 触发:[BUGS.md](../BUGS.md) BUG-19 与 BUG-9,用户指定的下一批。

## 为什么这两条合成一批

它们是同一种病的两例,而且落在**同一块界面**(牌桌的行动栏 + 免盲投票面板):

- **[BUG-19](../BUGS.md)**:合法加注下限是 `last_bet + max(last_raise_size, BB)`([rules.md](../../rules.md) ②),而 `last_raise_size` 从来没上过 wire。前端够不着,于是自己编了两个式子(输入框 `min={callAmount*2}`、留空回退 `lastBet+bigBlind`),别人大额加注之后都低于真下限,发出去被 `ILLEGAL_ACTION` 拒。
- **[BUG-9](../BUGS.md)**:`StateSnapshot` 不投影 `room.entry_vote`,于是重连/顶替之后进行中的免盲投票在客户端凭空消失,重连回来的**必需投票人**根本不知道有一张票在等他——全票制下投票就此卡死。

两条都是「客户端需要的一个事实没有传达渠道」。[0084](0084-new-here-channel.md)(`new_here`)、[0087](0087-reconnect-and-displacement-in-browser.md)(开局底池 / 本街下注态)已经是这条链上的第三、第四例;0087 还顺手证明了这类推断**恰好会在边界上错**,并且错得很难发现。

## 先读设计文档(本仓纪律)

- [rules.md](../../rules.md) ②「min-raise 与重开」:下限 `amount ≥ last_bet + max(last_raise_size, BB)`;**all-in 不受此限**(`amount == stack` 即便不足一个完整加注也放行,简化决策 2)。换街时 `last_bet=0`、`last_raise_size=BB`。
- [rules.md](../../rules.md) ①「免盲投票」:候选 = 当前 `new_here` 座位且在开票时**冻结**;投票人 = 非 `new_here` 且 `READY_TO_PLAY`,每次**实时重算**;全票 approve 才通过,任一 reject 立即失败。`voters` 实时重算这一点决定了快照必须**现算**而不是存一份。

## 打算怎么改

### 一、上 wire 的是**下限本身**,不是 `last_raise_size`

[BUGS.md](../BUGS.md) 当初写的是「把下限(或 `last_raise_size`)放上 wire」。选**下限**(`min_raise_to`):

- 给 `last_raise_size` 等于把公式留给客户端去套,而客户端套公式正是 0084/0087 反复出事的地方(漏一个 `max(..., BB)` 就错)。
- 下限是客户端**真正要用**的那个数:输入框的 `min`、留空时的默认值。
- 公式只留一份:抽 `betting.min_raise_target(hand, big_blind)`,校验与投影共用它——校验和显示由同一行代码算出,不可能对不上。

### 二、要动的消息(`last_bet` 会变的那几处)

| 消息 | 加什么 | 为什么 |
|---|---|---|
| `HandStarted` | `last_bet`、`min_raise_to` | 前端此前推 `lastBet = big_blind`(碰巧对,仍是推) |
| `HandStatusChanged` | `min_raise_to` | `last_bet` 已由 0087 补上 |
| `PlayerActed` | `min_raise_to` | 每次行动后下限都可能变(重开) |
| `StateSnapshot` | `last_bet`、`min_raise_to`、`free_entry_vote` | 前端此前推 `lastBet = max(bet_amount)`;投票投影即 BUG-9 |

`free_entry_vote` 用一个嵌套值对象 `FreeEntryVoteView`(candidates/voters/approvals),无投票进行时为 `null`;三个字段与 `FreeEntryVoteUpdated` 同义同源(同一个 core 投影 helper 算出来的)。

### 三、前端

删掉三处自算:`lastBet: msg.big_blind`、快照的 `lastBet = max(bet_amount)`、行动栏的 `min={callAmount*2}` 与回退 `lastBet + bigBlind`。免盲投票面板改为也接受快照里的投影,于是重连之后它还在。

## 要动的文件(预期)

- `app/core/rules/betting.py`(抽 `min_raise_target`)、`app/wire/server.py`、`app/core/reduce.py`
- codegen 产物 `frontend/src/types/wire.gen.ts`
- 前端 `src/store/room.ts`、`src/app/game/page.tsx`
- 测试:`tests/core/*`、`frontend/src/store/room.test.ts`、`frontend/e2e/*`
- 文档:[rules.md](../../rules.md)(下限怎么传达)、[core.md](../../core.md)、[wire-protocol-guide.md](../../wire-protocol-guide.md)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)、[frontend/docs/state.md](../../../../frontend/docs/state.md)、[BUGS.md](../BUGS.md)(划掉两条)、[TODO.md](../TODO.md)

## 实际改了什么

按计划落地,**多出一件**:BUG-9 的登记只写了「快照不投影 `entry_vote`」,而实测下来那只是它的一半。

### 一、加注下限(BUG-19)

- **`core/rules/betting.py`** 抽出 `min_raise_target(hand, big_blind)`,`_apply_bet` 的校验改调它。**公式只此一处**,所以「广播的下限」与「判定的下限」不可能分叉——这正是选它而不是选 `last_raise_size` 的理由。
- **四条消息各加 `min_raise_to`**:`HandStarted`(顺带补 `last_bet`,前端此前推 `= big_blind`)、`HandStatusChanged`、`PlayerActed`、`StateSnapshot`(顺带补 `last_bet`,前端此前推 `= max(bet_amount)`)。三个新字段一律**必填无默认**,漏填是编译期错误。
- **前端**删掉两个自编式子:输入框 `min={callAmount * 2}` → `min={state.minRaiseTo}`;留空回退 `state.lastBet + state.bigBlind` → `state.minRaiseTo`。

### 二、免盲投票投影(BUG-9)—— 登记漏了一半

- **`StateSnapshot` 加 `free_entry_vote: FreeEntryVoteView | None`**,由 `_entry_vote_view(room)` 现算(不能存一份:`voters` 按 [rules.md](../../rules.md) ① 每次实时重算)。
- **写浏览器用例时发现登记不完整**:快照投影只让面板「重连后还在」,但重连恢复的是 `SITTING_IN` 而不是 `READY_TO_PLAY`([connection.md](../../connection.md) 重连臂),所以人回来之后**不是合格投票人**;他再点一次 Ready 才是——而这件事此前**没有任何事件承载**(`_maybe_resolve_entry_vote` 原本只在「因此达成全票」时产 `Closed`,否则一个事件都不发)。结果是他的面板永远停在「你不是本次的投票人」,全票制下这张票照样卡死。
  - 修法:`_maybe_resolve_entry_vote` 在票**未终结**时补一条 `FreeEntryVoteUpdated`(当前公开态)。它的调用点已经覆盖离场/坐出/起身/准备,所以投票人集合的增减两个方向都被盖住。
  - 投影只有一处实现 `_entry_vote_projection(room)`,开票广播 / 进度广播 / 快照三处共用,口径不会各说各话。

### 三、顺带

- `scripts/gen_wire_ts.py` 的值对象注册表补 `FreeEntryVoteView`(不补就直接报「referenced types not registered」,守门有效)。
- `smoke:raise` 补一条断言:**服务器广播的 `min_raise_to` 就是它自己判定用的那个数**(同一局里 12 被拒、18 被接受,现在 `min_raise_to` 也必须是 18)。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **750 passed**(746 → 750) |
| 前端 vitest | **89 passed**(86 → 89) |
| 浏览器 `npm run test:e2e` | **15 passed** |
| `npm run smoke` / `smoke:raise` / `smoke:stale` | 全部通过(守恒 2000 → 2000) |
| 后端改完重启 uvicorn 再跑前端各层 | 是 |

**反向变异验证 6 处**,每处都确认「改回旧行为 → 对应测试变红」:

| 变异 | 变红的 |
|---|---|
| 前端输入框 `min` 退回 `callAmount * 2` | 浏览器 raise 用例(期望 18 实得 16) |
| 前端加注回退退回 `lastBet + bigBlind` | 浏览器 raise 用例(不填金额点 Raise → 底池没变) |
| 前端快照不投影 `free_entry_vote` | vitest 2 条 + 浏览器 vote 用例(重连后面板找不到) |
| 后端 `StateSnapshot.free_entry_vote` 硬写 `None` | core 1 条 |
| 后端 `_maybe_resolve_entry_vote` 不补发进度 | core 1 条(重连后再 Ready 拿不到投票权) |
| `min_raise_target` 丢掉 `max(..., BB)` | core 1 条 |
| `PlayerActed.min_raise_to` 改用近似式 `last_bet + BB` | 浏览器 raise 用例 + `smoke:raise`(min_raise_to=12,期望 18) |

## 自 review

按 [review.md](../../review.md) 七维。本批是**协议加字段 + core 投影**,最高风险面是「新字段的完备性」与「投影口径是否与判定一致」。

- **① 分层 / 不变量**:core 仍纯同步;`min_raise_target` 是 `rules/betting.py` 里的纯函数,校验与投影共用它,没有把公式复制到投影处。`_entry_vote_view` / `_entry_vote_projection` 是 core 内纯读 helper,不 raise。前端这次又是**被修复的一方**:两个自编式子删掉了,`freeEntryVote` 改成照抄服务器。
- **② 代码↔文档同步**:[rules.md](../../rules.md) ② 补「下限怎么传达 + 为什么不传 `last_raise_size`」;[core.md](../../core.md) 事件载荷补投票补发与快照投影的由来;[wire-protocol-guide.md](../../wire-protocol-guide.md) 四条消息的字段与一段 `min_raise_to` 的使用须知(含 **all-in 不受限**这条最容易漏的);[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 两条;[frontend/docs/state.md](../../../../frontend/docs/state.md)「行动按钮怎么算」那段原本白纸黑字写着让前端套公式,已改。
- **③ 文档↔文档一致**:[BUGS.md](../BUGS.md) 两条划掉并进「已修复」表;BUG-9 的登记**当场更正**——它写的修法(「加投影」)只是一半,不更正的话下一个人照抄就会留下卡死的票。[TODO.md](../TODO.md) 勾掉 0080·B 的 min-raise 余项。
- **④ 数据模型正确性**:上 wire 的是**下限本身**而不是 `last_raise_size` —— 后者是公式的原料,给了就等于请客户端重算规则(0084/0087/BUG-19 全是这么出的事)。`FreeEntryVoteView` 与 `FreeEntryVoteUpdated` 三字段同义,但由**同一个** `_entry_vote_projection` 算出,不构成第二份事实源;两种包装的理由是「一个是消息、一个是字段」,改 `FreeEntryVoteUpdated` 的形状会白白冲击已经在用的前端。`free_entry_vote` 为 `None` 表示「没有投票」,与 `hand_status=None` 同风格,不引入「有投票但字段为空」这种可表达的非法态。
- **⑤ 规范合规**:新字段/新值对象逐个带中文含义注释并指向 rules.md ②/①;无魔法数;注释讲「为什么」,尤其两处:为什么上 wire 的是下限不是原料、为什么快照必须现算投票人而不能存一份。
- **⑥ 测试充分**:7 处反向变异全部确认。**如实记缺口**:(a) `min_raise_to` 在 **all-in 不受限**这条上只有文档和 core 既有用例(②.7/②.8)保证,**没有**一条端到端用例走「筹码不够、直接 all-in 低于下限」——构造它要精确配筹码;(b) 投票**通过**的完整流程(全票 → 新人免盲入局)仍未在浏览器里走完,那是 [TODO](../TODO.md) 的 0082·B;(c) 投票人集合因**断线**而缩小时不补发(`_disconnect` 刻意不重算,见 [0074·B](0074-code-defect-hunt.md) 的误报留档),所以其他人的 `voters` 会短暂偏大,直到下一次状态变化——记档不修,因为一旦补发就等于承认「断线者不再是投票人」,而那正是 0074·B 判定为**有意设计**要避免的。
- **⑦ 流程账本**:本篇即账本,开工前写「打算」(含「上下限还是上原料」的取舍论证)、收工回填。与打算相比多出一件(BUG-9 的另一半),已单独成节并说明是怎么发现的。

### 顺带发现,未在本批处理

- **`ConnectionBanner` 与「重连后要重新 Ready」没有任何提示**:人回来之后座位还在、筹码还在,但状态是 `SITTING_IN`,不点 Ready 就不入局也不能投票。界面上没有一句话说这件事。属于体验缺口,不是缺陷。
- **`_disconnect` 期间的 `voters` 偏大**:见上 ⑥(c)。

### 收工后补记(账本对齐)

本批修掉的项在 [BUGS.md](../BUGS.md) 当时就划掉了,但 [TODO.md](../TODO.md) 里 **0072 那一节的镜像条目**当时漏了——同一件事在两处登记,只清一处就是新的漂移。已于 0093 一并补齐(见 [changes/0093](0093-ledger-alignment.md))。
