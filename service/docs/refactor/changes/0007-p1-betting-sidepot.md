# 0007 · P1(一):纯规则数学层 —— deck + betting + sidepot

日期:2026-06-17 · 范围:`service/app/core/deck.py`、`app/core/rules/betting.py`、`app/core/rules/sidepot.py`、`service/tests/core/`(+ `tests/builders.py`)

## 背景 / 打算改什么

进入 P1(core 规则,见 [TODO.md](../TODO.md))。P1 是重构主力,且最易出钱(积分)错。按 [rules.md](../rules.md) §定位「**先按本文写穷举测试,再写实现**」,本篇**只**落地三块**纯函数、零编排依赖**的规则数学,把 money-critical 的部分先用穷举测试钉死:

- `core/deck.py`:`random.SystemRandom` 洗牌(不变量 1 允许)+ treys `Evaluator` 单例;`evaluate(board, hole)`(分数越小越强)。`treys` 已是依赖,无需改 pyproject。
- `core/rules/betting.py`:rules.md **②** —— 三动作校验(FOLD/CHECK/BET)、min-raise/重开、短 all-in 不重开、`street_closed` 谓词(`has_acted`+ 跟平)、街结算(`bet_amount`→`contributed`、重置)、`next_active_position`。
- `core/rules/sidepot.py`:rules.md **③** —— 退还未叫注 → 分层削池 → 判池归属(treys 最小分胜出)+ 奇数零头(最接近庄家左手)。**牌力以分数 dict 传入**(`strength: nick→score`),使 sidepot 不直接依赖 treys、可用 rules.md 测试里的合成名次直接单测。

**本篇不含**:`rules/blinds.py`(定庄/盲位/入局防躲盲/免盲投票——与 `_start_hand`、`room.entry_vote` 编排纠缠,单列一篇)、`core/reduce.py`(顶层 match + 各 handler,依赖三块规则,单列一篇)。这样每篇是一个内聚、可 review 的工作单元(README §5)。

## 设计决策(开工前定的)

- **规则函数操作 `Hand`/`Player` 工作副本、原地改、返回 `Err | None`**,不产出 Event(Event 是 reduce 的活)。与 core 的「改工作副本 + Go 风格错误」一致,绝不 `raise`(硬规则 4)。
- **`betting.apply_action(hand, player, action, bet_amount, big_blind)`**:`big_blind` 显式传入(min-raise 下限要 `max(last_raise_size, BB)`),core 不读 `gameconfig`(域 dataclass 不烤死配置,见 0002)。
- **`sidepot.settle(...)` 收 `strength`/`seat_of`/`button_position`/`seat_size` 等快照入参**,返回 `Payout`(refunds / pots / winnings / total),纯计算、可断言守恒。reduce 调用时用 `deck.evaluate` 算 `strength`。这样 sidepot 既不碰 treys 也不碰 domain 之外的东西。
- **FOLD 仅当 `bet_amount < last_bet`**:按 rules.md ②「无注该 check」。强制 fold(超时/离桌 auto-fold)走 reduce、不经此校验(rules.md ④ / timer.md),本篇不涉及。
- **奇数零头**:按 rules.md ③ 公式 `(seat-button)%seat_size` 升序取第一个,整份零头给它(公式权威,文字「左手」以公式为准)。

## 实际改了什么

新增(全部按计划落地):

- `app/core/deck.py`:`FULL_DECK`(52 张)、`shuffled_deck()`(从副本洗,不动 FULL_DECK)、`evaluate(board, hole)`(treys 单例,越小越强)。`random.SystemRandom` + treys 都是不变量 1 允许的本地纯计算。
- `app/core/rules/__init__.py`(空包)。
- `app/core/rules/betting.py`:`apply_action`(FOLD/CHECK/BET + min-raise/重开/短 all-in)、`street_closed`、`settle_street`、`next_active_position`。
- `app/core/rules/sidepot.py`:`SidePot`/`Payout` + `settle(...)`(退还未叫注 → 分层削池 → 判池 + 奇数零头),牌力以 `strength` 快照入参。
- `tests/core/test_deck.py`(3)、`test_betting.py`(22)、`test_sidepot.py`(9);`tests/builders.py` 加 `player()`/`hand()` builder。**58 测试全绿**(P0 的 24 + 本篇 34);core 纯度校验通过。

### 实现期自查发现并当场修的两个 bug

1. **sidepot 分层 `prev` 未前进**:空 eligible 退化分支 `continue` 时漏了 `prev = level`,会让下一档 `per` 算错。改成每档开头无条件 `prev = level`。
2. **`next_active_position` 环回自身**:`range(1, size+1)` 会在只剩自己 ACTIVE 时绕回自身;改 `range(1, size)`——返回「下一个**其他** ACTIVE」,无则 None(是否结束由 reduce 查 `street_closed` 定)。

### 偏离设计 / 决策

- **退化空 eligible 子池**:rules.md ③ 说「按 contributors 原额退回 + 落 ERROR」。core 不能 IO(硬规则 1),所以按本档 `per` 退回各 contributor(守恒),把「识别异常」留给 reduce(它持 live/contributed 快照,可据「refund 落到弃牌者」识别)。这是对 rules.md 文字的实现侧澄清,不改行为语义,故未改 rules.md;若日后要更强信号再议。

## 自 review(0007 后,多 agent 对照 rules.md ②/③ + 对抗式核实)

10 条候选、3 条确认、7 条驳回(驳回的多是 reduce 层顺序问题——已 deferred、或对 rules.md 文字的误读)。确认并已修:

1. **betting.py / sidepot.py 文件头有模块 docstring** —— 违反 coding_principle L41「不在文件开头写整段解释模块职责/不变量的 docstring」(刚在 0006 强化的规矩),且 P0 邻里无模块 docstring。改成单行 `# rules.md ②/③ …` 指针注释。
2. **settle_street 缺「弃牌者带本街 bet_amount」的守恒测试** —— 代码本就正确,但旧测试的弃牌者 bet=0,挡不住「丢弃弃牌者投入」的回归。补 `test_settle_street_merges_folded_players_mid_street_bet`。
3. **②.7 短 all-in 测试漏断言 `has_acted=True`** —— 补上,完整对齐 rules.md ② L126。
（另把 `next_active_position` 注释里「街即关闭」的不精确措辞改准。)

## 待办 / 下一步

- P1(二):`rules/blinds.py` + 免盲投票状态。
- P1(三):`core/reduce.py` 顶层 + 各 handler,串起三块规则 + deck + 工作副本。
- 守恒/隐私断言在 reduce 落地时默认开(测试期)。
