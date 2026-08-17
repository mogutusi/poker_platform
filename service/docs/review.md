# 提交前复审(review)

## 一句话定位

每次 commit / push 前,对照本篇做一次对抗式自 review。

结论写进当前 [changes/](refactor/changes/) 的 `NNNN`「自 review」段,无此段不 push。测试全绿是必要条件、不是充分条件——下面的案例全是测试全绿后被 review 抓到的。

> 前置:[coding_principle.md](coding_principle.md)(硬规则 + 快速自检表)、[refactor/README.md](refactor/README.md) §0/§5(批判性思考 + 工作流)、[architecture.md](architecture.md)(不变量出处)。

## 为什么(重构实战:每次 review 都抓到东西)

P0–P1 每次 review 都在测试全绿之上抓出缺陷:

- [0003](refactor/changes/0003-p0-review.md):代码对照文档 9 处漂移,例如类型放松了不变量、wire 码大小写不一致、事件缺字段。
- [0006](refactor/changes/0006-p0-review-followup.md):4 处,其中两处与「已同步」的自我声明矛盾。
- [0009](refactor/changes/0009-holistic-review-cleanup.md):16 个候选、9 处确认,包括 34 条死链、FOLD 条件文档与代码矛盾、奇数零头环绕漏测。
- [0010](refactor/changes/0010-p1-reduce-start-hand.md) / [0011](refactor/changes/0011-p1-player-action-showdown.md):抓到 bootstrap 看整桌防躲盲、短牌堆守 Err、事件顺序断言、money path 的守恒/退还边界。

结论:测试覆盖「想到的」,review 覆盖「没想到的」和文档/流程一致性。两者不可互替。

## 怎么 review(方法)

四步:定范围 → 挑高风险面 → 逐条反驳 → 落进变更记录。

1. **范围 = 本次 diff + 它的契约消费方**,不必每次全仓扫:改了事件就查 dispatch/Timer 怎么用它,改了 wire 码就查前端契约,改了结算就查守恒各方。
2. **重点放在本次改动的最高风险面**。涉及钱(积分/边池/结算)、隐私(底牌/牌堆)、并发(await/锁/顺序)、不变量的改动优先深挖(见 0011「money path 重点核对」)。
3. **对抗式核实**:每个候选发现先试图反驳它——字面对不对、影响真不真。驳不倒才算数,才修或才记录(0009 的做法)。假阳性不进记录。
4. **结论写进变更记录**:确认项当场修(改代码,同时同步文档),驳回项要记,「本可查但消费方未落地」的覆盖空缺也要记;都写进 `changes/NNNN`「自 review」段——讨论的产物是文档,不是散落的对话(README §5)。

## 复审维度(逐维过一遍 · 附实战例)

七个维度,每次逐维过一遍。

| 维度 | 查什么 | 实战抓到过 |
|---|---|---|
| ① 分层 / 不变量 | core 纯同步、不 import shell/db;工作副本回滚;helper 不 raise | `checkout/commit` 留在 shell 模块函数;`Work` 类型上移,避免 core import shell([0003](refactor/changes/0003-p0-review.md) / [0010](refactor/changes/0010-p1-reduce-start-hand.md)) |
| ② 代码↔文档同步 | 实现偏离签名/字段/结构 → 同次改文档 | FOLD 条件 core.md↔`betting` 矛盾;`TurnChanged` 缺字段或多字段([0009](refactor/changes/0009-holistic-review-cleanup.md) / [0003](refactor/changes/0003-p0-review.md) / [0006](refactor/changes/0006-p0-review-followup.md)) |
| ③ 文档↔文档一致 | 跨文档链接、伪码字段、计数 | 34 条死链 `../→../../`;伪码 `timeout_s` 字段数;TODO 测试计数误标([0009](refactor/changes/0009-holistic-review-cleanup.md) / [0006](refactor/changes/0006-p0-review-followup.md)) |
| ④ 数据模型正确性 | 别把不可能态变成可表达;别用过严类型卡住合理用法 | `UserState.room: str\|None` 放松了「UserState⇒在房」;`Err.detail` 必填卡掉无 detail 用法([0003](refactor/changes/0003-p0-review.md)) |
| ⑤ 规范合规 | 字段/枚举注释、命名、`Err` 风格、无魔法数/死代码 | `Hand.flop/turn/river` 漏注释;自文档化枚举的注释例外([0006](refactor/changes/0006-p0-review-followup.md)) |
| ⑥ 测试充分 | 守恒/隐私默认开;真空真、边界、环绕;别与已有测试重复 | 奇数零头 `button≠0` 环绕漏测;rollback 对照测试;`street_closed` 真空真([0009](refactor/changes/0009-holistic-review-cleanup.md) / [0011](refactor/changes/0011-p1-player-action-showdown.md)) |
| ⑦ 流程账本 | `changes/NNNN` 回填(打算↔实际差异)、TODO 勾项、提交引用 `NNNN` + 全英文 | 「已同步」声明与实际漂移矛盾([0006](refactor/changes/0006-p0-review-followup.md));提交规约见 [dev.md](dev.md) |

## core 正确性红线(改 `reduce` / `shell` 必逐条核)

承接 [coding_principle.md](coding_principle.md) 硬规则与[架构契约](architecture.md)。改到核心路径时逐条核对——测试可能没覆盖到。

- **纯同步**:无 `await`/IO/DB/`sleep`/读墙钟;core 无 shell/fastapi/sqlalchemy/websocket import。用 `grep` 复验。
- **回滚**:只改工作副本(本命令的状态草稿);失败 `return [], Err`,不 `raise`,不留半改。建议先校验后改,图清晰。
- **筹码守恒**:`Σ points + Σ bet_amount + Σ contributed == 锁入总额`;结算后 `Σ 还回 Seat == 同额`。每分支可 `assert`(测试期)。
- **隐私**:`hole_cards`/`deck` 只出现在 `Personal(HoleCards)` 与 `Broadcast(HandShowDown)`。其余事件/日志/落库在结构上就没有这些字段。
- **身份**:取连接绑定的 nick,不信报文自报。
- **顺序**:事件顺序契约见 core.md §事件。
- **新鲜度**:`epoch` 单调;过期 `Timeout` 被 staleness 检查(过期作废)挡掉。
- **落库**:`Persist` 同步进缓冲,`put_*` 无 `await`;状态写覆盖、事件写追加、回灌按「更新者优先」(见 [db.md](db.md))。

## 提交门槛(必须守住)

1. submit 前必 review,结论写进「自 review」段(见「一句话定位」)。
2. 测试全绿仍要 review:维度 ②③⑦ 测试根本不覆盖。
3. 范围聚焦:本次 diff 的最高风险面 + 契约消费方。不求全仓,但该查的都要查到。

## 与其它文档

- 流程位置:[refactor/README.md](refactor/README.md) §5「收工前」的必经一步;提交规约见 [dev.md](dev.md)。
- 快速硬规则自检:[coding_principle.md](coding_principle.md)「提交前自检」是本篇维度 ① 的速查子集;本篇是完整复审。
- 测试:[testing.md](testing.md)——与 review 不可互替。
