# 提交前复审(review)

## 一句话定位

**每次 submit(commit / push)前,对照本篇做一次对抗式自 review,并把结论记进当前 [changes/](refactor/changes/) 的 `NNNN`「自 review」段。** review 不是可选润色,是**提交门槛**——与「变更记录先行」「提交信息全英文」同级纪律。**绿测是必要非充分条件:测试全绿仍要 review**(下面每条都是「测试全绿却仍被 review 抓到」的真实案例)。

> 前置:[coding_principle.md](coding_principle.md)(硬规则 + 快速自检表)、[refactor/README.md](refactor/README.md) §0/§5(批判性思考 + 工作流)、[architecture.md](architecture.md)(不变量出处)。本篇是「提交前那一道关」的方法与清单,从 [0003](refactor/changes/0003-p0-review.md)/[0006](refactor/changes/0006-p0-review-followup.md)/[0009](refactor/changes/0009-holistic-review-cleanup.md)/[0010](refactor/changes/0010-p1-reduce-start-hand.md)/[0011](refactor/changes/0011-p1-player-action-showdown.md) 的实战 review 提炼。

## 为什么(重构实战:每次 review 都抓到东西)

P0–P1 的每一次 review 都在「测试全绿」之上抓出了缺陷,无一空手:

- **0003**:P0 代码对照文档,9 处漂移(类型放松不变量、wire 码大小写、缺事件字段…)。
- **0006**:push 前多视角 review,4 处(漏注释字段、文档伪码失同步…),且两处与「已同步」的自我声明矛盾。
- **0009**:reduce 前整体复审,16 候选 / 9 确认(34 条死链、FOLD 条件文档↔代码矛盾、奇数零头环绕漏测…)。
- **0010 / 0011**:push 前自 review,抓到 bootstrap 看整桌防躲盲、短牌堆守 Err、事件顺序断言、money path 守恒/退还边界。

**结论:绿测覆盖「我想到的」,review 覆盖「我没想到的 + 文档/流程卫生」。两者不可互替。**

## 怎么 review(方法)

1. **范围 = 本次 diff + 它的契约消费方**,不必每次全仓扫。改了事件就查 dispatch/Timer 怎么用它;改了 wire 码就查前端契约;改了结算就查守恒方。
2. **重点压在本次改动的最高风险面**:涉及钱(积分/边池/结算)、隐私(底牌/牌堆)、并发(await/锁/顺序)、不变量的改动优先深挖(见 0011「money path 重点核对」)。
3. **对抗式核实**:每个候选发现**默认先试图反驳它**(字面对不对 + 影响真不真),驳不倒才算数、才修或才记(见 0009 的「2 个默认反驳核实者双签」)。这样既不漏、也不堆假阳性。
4. **结论入账**:确认项当场修(代码 + 同步文档);驳回项与「本可查但消费方未落地」的覆盖空缺,写进 `changes/NNNN`「自 review」段——讨论的产物是文档,不是散落的对话(README §5)。

## 复审维度(逐维过一遍 · 附实战例)

| 维度 | 查什么 | 实战抓到过 |
|---|---|---|
| **① 分层 / 不变量** | core 纯同步、不 import shell/db;工作副本回滚;helper 不 raise | `checkout/commit` 留 shell 模块函数、`Work` 类型上移免 core import shell([0003](refactor/changes/0003-p0-review.md)/[0010](refactor/changes/0010-p1-reduce-start-hand.md)) |
| **② 代码↔文档同步** | 实现偏离签名/字段/结构 → 同次改文档 | FOLD 条件 core.md↔`betting` 矛盾、`TurnChanged` 缺/多字段([0009](refactor/changes/0009-holistic-review-cleanup.md)/[0003](refactor/changes/0003-p0-review.md)/[0006](refactor/changes/0006-p0-review-followup.md)) |
| **③ 文档↔文档一致** | 跨文档链接、伪码字段、计数 | 34 条死链 `../→../../`、伪码 `timeout_s` 字段数、TODO 测试计数误标([0009](refactor/changes/0009-holistic-review-cleanup.md)/[0006](refactor/changes/0006-p0-review-followup.md)) |
| **④ 数据模型正确性** | 别把不可能态变可表达;别用过严类型卡合理用法 | `UserState.room: str\|None` 放松「UserState⇒在房」、`Err.detail` 必填卡掉无 detail 用法([0003](refactor/changes/0003-p0-review.md)) |
| **⑤ 规范合规** | 字段/枚举注释、命名、`Err` 风格、无魔法数/死代码 | `Hand.flop/turn/river` 漏注释、自文档化枚举注释例外([0006](refactor/changes/0006-p0-review-followup.md)) |
| **⑥ 测试充分** | 守恒/隐私默认开;真空真、边界、环绕;别与已有测试重复 | 奇数零头 `button≠0` 环绕漏测、rollback 对照测试、`street_closed` 真空真([0009](refactor/changes/0009-holistic-review-cleanup.md)/[0011](refactor/changes/0011-p1-player-action-showdown.md)) |
| **⑦ 流程账本** | `changes/NNNN` 回填(打算↔实际差异)、TODO 勾项、提交引用 `NNNN` + 全英文 | 「已同步」声明与实际漂移矛盾([0006](refactor/changes/0006-p0-review-followup.md));提交规约见 [dev.md](dev.md) |

## core 正确性红线(改 `reduce` / `shell` 必逐条核)

承 [coding_principle.md](coding_principle.md) 硬规则 + [架构契约](architecture.md),改到核心路径时这几条**逐条核**(测试可能没覆盖到):

- **纯同步**:无 `await`/IO/DB/`sleep`/读墙钟;core 无 shell/fastapi/sqlalchemy/websocket import(`grep` 复验)。
- **回滚**:只改工作副本,失败 `return [], Err`、不 `raise`、不留半改;先校验后改求清晰。
- **筹码守恒**:`Σ points + Σ bet_amount + Σ contributed == 锁入总额`;结算后 `Σ 还回 Seat == 同额`。每分支可 `assert`(测试期)。
- **隐私**:`hole_cards`/`deck` 只在 `Personal(HoleCards)` 与 `Broadcast(HandShowDown)`;其余事件/日志/落库结构上无此字段。
- **身份 / 顺序 / 新鲜度**:身份取连接绑定 nick(不信报文);事件顺序契约(core.md §事件);`epoch` 单调、过期 `Timeout` 被 staleness 挡。
- **落库**:`Persist` 同步入缓冲、`put_*` 无 `await`;状态写覆盖、事件写追加、回灌「更新者优先」(见 [db.md](db.md))。

## 提交门槛(必须守住)

1. **submit 前必 review**,结论记进当前 [changes/](refactor/changes/) 的 `NNNN`「自 review」段;**无此段不 push**。
2. **绿测必要非充分**:测试全绿仍要 review(维度 ②③⑦ 测试根本不覆盖)。
3. **对抗核实**:候选发现默认先反驳,驳不倒才修 / 才记;假阳性别塞进记录。
4. **确认即修 + 同步文档**:代码改了真相,对应设计文档同次跟上(见 coding_principle「双向同步」)。
5. **范围聚焦**:本次 diff 的最高风险面 + 契约消费方,不求全仓但求「该查的都查到」。

## 与其它文档

- **流程位置**:[refactor/README.md](refactor/README.md) §5「收工前」的必经一步;提交(commit / push)规约见 [dev.md](dev.md)。
- **快速硬规则自检**:[coding_principle.md](coding_principle.md)「提交前自检」是本篇维度 ① 的速查子集;本篇是完整复审。
- **测试**:[testing.md](testing.md)——review 不替代测试,测试不替代 review。
- **不变量出处**:[architecture.md](architecture.md)。
