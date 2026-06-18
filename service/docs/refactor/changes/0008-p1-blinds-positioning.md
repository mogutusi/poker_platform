# 0008 · P1(二之一):开局定位与下盲 —— blinds.py(定庄/盲位/heads-up/排座 + 下盲)

日期:2026-06-18 · 范围:`service/app/core/rules/blinds.py`、`service/tests/core/test_blinds.py`

## 背景 / 打算改什么

P1(二)原计划把 `blinds.py` 一篇做完「定庄/盲位/heads-up + 入局付盲即玩/等大盲 + 免盲投票」(见 [rules.md](../../rules.md) ①、[TODO.md](../TODO.md))。开工前按 [README §0](../README.md) 质疑这一拆分,把它**拆成两步**:

- **定庄/盲位/排座/下盲**(rules.md ①.1-①.5)是**纯定位数学**:与 reduce 无耦合、可独立穷举单测、且 `_start_hand`(见 [core.md](../../core.md) §1)必然原样调用——核心稳定面。**本篇(0008)只落地这块。**
- **入局/防躲盲**(①.6-①.11)与**免盲投票**(①.12-①.15)是**跨手状态 + 命令状态机**:eligibility 读 `Seat.new_here`/`wait_for_big_blind`/`Room.waive_entry_for`(由 reduce 跨手维护),「等大盲」语义还与定位互锁;免盲投票是 `OpenFreeEntryVote`/`VoteFreeEntry` 驱动的 `EntryVote` 状态机 + `waive_entry_for` 快照。在 reduce(`_start_hand` + 投票 handler)缺位时单独把它们落地 = 在真空里定接口、易返工(正是 0007 当时把 blinds deferred 的「编排纠缠」原因)。**留到与 reduce 合篇。**

接缝很干净:定位函数把「本手在局的座位集合 `eligible`」当**入参**收,「谁在局」由 reduce 的 eligibility 算(后续),定位本身不关心怎么算出来的。

## 设计决策(开工前定的)

- `blinds.py` 三个**纯函数**,操作 seat 下标 / `Hand` / `Player` 工作副本、原地改、无 IO、不 `raise`(同 betting):
  - `advance_button(current_button, eligible)` → 下一个在局座位(环形;`eligible` = 本手在局座位下标集合)。
  - `seat_order(button, eligible)` → 行动序座位下标:`players[0]=SB`、`players[1]=BB`;**heads-up 特例** `[button(=SB), other(=BB)]`,其余「庄之后→庄」(SB=庄下一位)。
  - `post_blinds(hand, small_blind)` → `players[0]` 投 SB、`players[1]` 投 BB;短码 `min(blind, points)` 全下置 `ALLIN`;set `hand.last_bet=BB`、`last_raise_size=BB`;盲注**不置 `has_acted`**(SB/BB 还没自愿行动,尤其 BB 保留 preflop 选择权,见 rules.md ②)。
- **大盲 = 2×小盲**:具名常量 `BIG_BLIND_MULTIPLE = 2`(本平台定义,见 rules.md ① / `domain.Seat`),不留裸字面量(coding_principle 无魔法数字)。
- **行动者不在本模块**:preflop 首行动 = `betting.next_active_position(hand, 1)`(大盲下一位);postflop 首行动 = `betting.next_active_position(hand, 庄在 players 的下标)`。**复用 betting,不重复实现**;reduce 调用、街推进时落地。

## 实际改了什么

新增(全部按计划落地):

- `app/core/rules/blinds.py`:`advance_button`、`seat_order`(含 heads-up 特例)、`post_blinds`(短码 all-in)+ `BIG_BLIND_MULTIPLE`。
- `tests/core/test_blinds.py`(7):3 人/6 人/heads-up 定位、庄推进跳过非 ready + 环形回绕、短码盲注 all-in、下盲常规(不置 `has_acted`)。其中 6 人/heads-up 用例顺带用 `betting.next_active_position` 断言 preflop/postflop 首行动位,锁住「行动者复用 betting」的接缝。**65 测试全绿**(0007 的 58 + 本篇 7)。

文档同步:
- `rules.md` 未改(本篇行为完全落在 ① 已定义范围,无签名/结构偏离)。
- `core.md` §1 step 4(下盲)原写「更新 `bet_amount` / `contributed`」——与本篇 `post_blinds` 只写 `bet_amount`(`contributed` 街结束才并入,见 rules.md ②/③)及 0007 既有行为不符,**当场改为「更新各自 `bet_amount`(本街;街结束才并入 `contributed`)」**(push 前复审发现,见下)。

## 自 review(push 前多 agent 对抗式 + 人工:4 维 × 2 refute-by-default 核实)

**12 条候选、1 条确认、11 条驳回**。65 测试仍全绿、core 纯度通过。

确认并已修:

1. **core.md §1 step 4 文档失同步**:`post_blinds` 只写 `bet_amount`,而 core.md 仍写「`bet_amount` / `contributed`」(`contributed` 实际由 `settle_street` 在街结束并入)。本篇开工时只核对了 rules.md、漏看 core.md,故 0008 一度误称「无需改文档」;已改 core.md L92 + 上文「文档同步」。

11 条驳回均为**已正确代码上的测试加固/防御性建议**(核实者给出反例轨迹/组合证明):seat_order 在 `button∉eligible`(前置条件违反、reduce 必先 advance_button 保证)时的分支不对称属不可达路径;postflop「跳过弃牌」由 betting.next_active_position owning 的 0007 测试覆盖;短码只测 BB、`_post` 是 SB/BB 共用且已被钉常量,等等。均非缺陷,部分留作后续 test-hardening(见待办)。

## 待办 / 下一步

- **入局资格 / 防躲盲**(rules.md ①.6-①.11:付盲即玩 / 等大盲 / 换座·退房·坐出躲盲被堵 / bootstrap):与 reduce `_start_hand` 合篇;eligibility 产出「本手在局座位集合」喂给本篇定位函数。
- **免盲投票**(①.12-①.15):`OpenFreeEntryVote`/`VoteFreeEntry` handler + `EntryVote` tally + `waive_entry_for` 快照,与 reduce 投票 handler 合篇。
- **postflop 首行动**随街推进(core.md §3)落地,复用 `next_active_position(hand, 庄下标)`。
- **测试加固(复审驳回项,代码已验证正确,非阻塞)**:`seat_order` 的 heads-up `button=高位` 分支、`button=max(eligible)` 的空尾环绕、非连续座位全序断言;`post_blinds` 的 SB 短码 / 双短码用例 + `points+bet_amount==原始栈` 守恒显式断言。
