# 0023 · P1 余项:等大盲再入局时机 + 躲盲被堵(rules.md ①.7-①.10)

日期:2026-06-23 · 范围:`app/core/rules/blinds.py`(新 `sweep_entrant`)、`app/core/reduce.py`(`_start_hand`/`_eligible_seats` 重构 + `new_here` 重算 + `_sit_down` 透传)、`app/core/commands.py`(`SitDown.wait_for_big_blind`)、`app/wire/client.py`(wire `SitDown` + 映射)、`frontend/src/types/wire.gen.ts`(重生成)、`tests/core/test_wait_for_big_blind.py`(新)、`tests/core/test_start_hand.py`(注释/微调)、文档同步(rules.md / core.md / wire-protocol-guide.md / TODO)。

## 背景 / 打算改什么

[TODO](../TODO.md) P1 余项里最久悬的一项(0008→0022 七篇变更一直 defer):**等大盲再入局时机 + 躲盲被堵**([rules.md](../../rules.md) ①.7-①.10)。基础设施早已就位(`Seat.wait_for_big_blind`/`new_here`、`blinds` 定位函数把 `eligible` 当入参、`_start_hand`/`_eligible_seats` 已正确把 `wait_for_big_blind` 座位排除发牌),独缺**入局时机**这块从未写的逻辑。

按 [README §0](../README.md) 先质疑粒度:这一项是一个**完整工作单元**——「等大盲入局时机」(PART A)、「上一手没参与即算 new_here」(PART B 防躲盲)、「让玩家能选等大盲」(PART C wire 切片)三块互锁,合篇落地才自洽(只落 A 没 C = 死代码;只落 A 没 B = 留躲盲洞)。能脱 DB/WS 纯单测。

### 三块设计(开工前定,经对抗式设计核验后锁定)

> 本设计跑了一轮多 agent 对抗核验(3 视角:BB-sweep 正确性/终止性、防躲盲完备性、不变量涟漪)。核验给出 **SOUND-WITH-FIXES**:核心 fixpoint 规则、option(a) 重算、wire 默认全部正确,但提了 1 个崩溃 bug(FIX-1)+ 多候选解析需精确化(FIX-2)。下方为锁定后的精确算法。

**PART A · BB-sweep 入局(解循环依赖)**
「等大盲」者(`READY` 且 `new_here` 且 `wait_for_big_blind`、不在 `waive_entry_for`、非 bootstrap)**当且仅当本手会成为大盲(`order[1]`)时免费入局**——他作为结构大盲下的那一个 BB **就是**入局费,不额外 post。

循环依赖:「候选是否入局」取决于「谁是大盲」,而「谁是大盲」又取决于「候选是否入局」。解法是一个 **fixpoint over 标准 `seat_order`**:
- `core_dealt` = 今天 `_eligible_seats` 的发牌集(established / 付盲即玩 / bootstrap / waive),**不含等大盲 waiter**。`waiters` = 上述「等大盲」者座位集。
- `button = advance_button(prev_button, core_dealt)` —— **庄位永远在 `core_dealt` 上定**(FIX-1/FIX-2 关键:waiter 永不持庄/小盲,只可能当大盲)。
- `entrant = sweep_entrant(button, core_dealt, waiters)`:
  - `qualifiers = { w∈waiters : seat_order(button, core_dealt ∪ {w})[1] == w }`(单看每个 waiter,加进去会不会正好是大盲)。
  - 多个 qualifiers 时,真正入局的唯一大盲 = `seat_order(button, core_dealt ∪ qualifiers)[1]`(最靠小盲那个);其余 waiter 下手随庄推进再轮到(不饿死、不瞬移)。
- `dealt = core_dealt ∪ {entrant?}`;`order = seat_order(button, dealt)`,entrant 即 `order[1]`、由 `post_blinds` 下结构大盲。
- **FIX-1**:`len(core_dealt) < 1` 时**不调 `advance_button`**(空集 `sorted(set())[0]` 会 IndexError、破 core「helper 绝不 raise」)→ 直接 `NOT_ENOUGH_PLAYERS`。waiter 单独不能 bootstrap(无「免费空降好位」不变量)。
- **庄位必须在 `core_dealt` 上定、绝不在 `dealt`(并集)上重算**:并集里 entrant 可能恰是 `prev_button` 后第一个座位 → `advance_button(prev, dealt)` 会误把 waiter 当庄(实测反例:core={0,2}、prev=2 → 在 core 上庄=0,在并集 {0,2,3} 上庄会变 3)。故把庄位计算从 `_start_hand` 收进取 `core_dealt` 的接缝,`seat_order` 直接吃这个庄。

**PART B · 「上一手是否参与」重算 new_here(防躲盲,option a)**
rules.md 行 50 明示判据是「上一手是否参与」而非只看 `new_here` 标志。落地:**`_start_hand` 末尾把本手未被发牌的每个在座者置 `new_here=True`**(发牌者仍在 175 行附近置 `False`)。
- 这把「换座 / 退房再进 / 坐出再回 / 没 ready 干等一手 / 断线跨手」**全部**统一成「没参与上一手 → 下手付盲或等大盲」,airtight。
- option (b)「只在进 SITTING_OUT 时标」/ (c)「只在 SITTING_OUT→SITTING_IN 回来时标」都漏掉「READY→SITTING_IN→READY 干等躲盲」「断线跨手 OFFLINE→SITTING_IN 躲盲」两条(玩家全程没碰 SITTING_OUT)。故选 (a)。
- **键于「发牌座位集」,非 UserStatus 启发式**:本手被发牌、手尾才坐出者(`sitting_out_next`)本手算参与了,手尾应 `new_here=False`;到**下手**真没被发牌时才翻 `True`。
- **不过度收费**:连续每手被发牌者永远在 `dealt` 里 → 永不进重算分支 → 恒 `new_here=False`。
- **不误触 bootstrap / voters**:重算在 `_eligible_seats`(读 pre-hand `new_here`)**之后**跑,故不影响本手 dealt/bootstrap;被发牌的 ≥2 人仍 `new_here=False` 带入下手 ⇒ 下手非 bootstrap(除非发牌者全离桌,那时 bootstrap 本就该真);READY 的 established 必被发牌,故 (a) 不会把 READY 投票人挤出 `_voters`。

**PART C · wire 切片(让玩家能选)**
`SitDown` 加 `wait_for_big_blind: bool = False`(core 命令 + wire 报文 + `to_command` + `_sit_down` 透传 Seat)。坐下即声明入局方式(覆盖首次入座/换座/退房再进——皆走 SitDown);**坐出再回默认付盲即玩**(不重新声明,见待办)。重生成 `wire.gen.ts`(漂移守门测试兜)。

### 残留简化(本篇明示,文档化)

1. **空 core 停摆是有意的**:唯一 established 全坐出/断线、只剩等大盲 waiter `READY` 时,`NOT_ENOUGH_PLAYERS` 等待,不让 waiter bootstrap(no-free-airdrop)。任一 established 再 ready / 来个付盲者即自愈。FIX-1 只防它崩,不改语义。
2. **单 established + 单 waiter 仍下结构大盲**:选「等大盲免费」者在与单个 established 打 heads-up 时照样下一个真实(结构)大盲——结构大盲**就是**入局费(rules.md ①.(b))。最反直觉但正确,Test C 钉。
3. **坐出后变免盲候选**:option(a) 把坐出 established 标 `new_here=True` → 成 `_free_entry_candidates`。consensual 且无害(免盲只写 `waive_entry_for`,他仍须 READY 被发牌才消费;他确实欠一个入局)。
4. **任何原因错过一手都收入局费**(坐出/断线/没 ready),无意图区分——这正是防躲盲 airtight 的根因(无 UserStatus 启发式可钻)。断线跨手要补一个大盲入局,文档化。

## 实际改了什么

**core(`app/core/`)**:

- `rules/blinds.py`:新 `sweep_entrant(button, core_dealt, waiters) -> int | None`——纯座位下标 fixpoint。`qualifiers = {w : seat_order(button, core_dealt ∪ {w})[1] == w}`(单看每个 waiter 是否正好当大盲),真正入局者 = `seat_order(button, core_dealt ∪ qualifiers)[1]`(最靠小盲那个),`else None`。注明前置(core_dealt 非空 + button ∈ core_dealt,调用方守)与「庄位定于 core_dealt」不变量。
- `reduce._eligible_seats`:签名从返回 `(dealt, paying)` 改为三分类 `(core_dealt, paying_entrants, waiters)`——`waiters` = `READY` 且 `new_here` 且 `wait_for_big_blind` 且不在 waive、非 bootstrap 的座位(其余仍按原逻辑进 core_dealt / paying)。
- `reduce._start_hand`:**庄位改在 `core_dealt` 上推进**(不在含 waiter 的并集上,否则 `advance_button` 可能把 waiter 当庄——实测反例 core={0,2}/prev=2);`entrant = blinds.sweep_entrant(...)`,`dealt = core_dealt ∪ {entrant?}`;**FIX-1**:`len(core_dealt) < 1` 前置返 `NOT_ENOUGH_PLAYERS`(空集 `advance_button` 会 IndexError、破「helper 绝不 raise」)。末尾加 **PART B 重标**:本手未被发牌的每个在座者置 `new_here=True`(发牌者仍清 `False`),跑在 eligibility/bootstrap 读 `new_here` 之后。
- `reduce._sit_down`:`Seat(..., wait_for_big_blind=cmd.wait_for_big_blind)` 透传声明的入局方式。
- `commands.SitDown`:+`wait_for_big_blind: bool = False`(默认付盲即玩)。

**wire(`app/wire/`)**:`client.SitDown` +`wait_for_big_blind: bool = False` + `to_command` 透传;`scripts/gen_wire_ts.py` 重生成 `frontend/src/types/wire.gen.ts`(`sit_down` 多 `wait_for_big_blind?: boolean`,漂移守门 `test_codegen_uptodate` 兜)。

**文档同步**:`rules.md` ① 加「实现细节(0023)」(sweep fixpoint / 庄位定于 core_dealt / PART B 重标 / waive 优先 / 三条残留简化)+ `SitDown.wait_for_big_blind` 标志 + 位次/默认行措辞;`core.md` §1 step 1(庄推进于发牌座位)/ step 6(未发牌者重标 new_here)/ 命令表 `SitDown(seat, wait_for_big_blind)`;`wire-protocol-guide.md` `sit_down` 行加字段;`TODO.md` 勾项 + 进度。

**计划外偏离**:无架构偏离。设计先经一轮多 agent 对抗核验锁定(SOUND-WITH-FIXES:FIX-1 空 core 守门、多候选取 `order[1]`),实现照锁定算法落地;核验给的「并集简化式」实测有反例(core={0,5}/button=0 误判),弃用,采 per-waiter fixpoint。

## 测试

`tests/core/test_wait_for_big_blind.py`(15 测试),**全量 229 绿**(0022 的 214 + 本篇 15)。覆盖:

- **①.7 等大盲入局**:大盲扫到其座 → 免费下结构大盲入局(`bet==BB`、`points==98` 非 96 钉「不双重 post」、守恒)、非大盲位(会当小盲那手)不发牌、heads-up core 翻 3 人(SB/庄仍 core 座、waiter 当大盲)、单 established + 单 waiter heads-up(结构大盲即入局费)、双 waiter 取最靠小盲入局 + 靠后 waiter 随庄推进再入局(不饿死)、**入局者是 order[1] 真正大盲而非最小座号**(杀 `min(qualifiers)` 变异)。
- **FIX-1**:唯一 established 坐出、只剩 READY 等大盲者 → `NOT_ENOUGH_PLAYERS` 且不抛 IndexError、world 不动。
- **PART B 防躲盲**:未发牌的坐出/没 ready 干等者一律重标 `new_here=True`;键于发牌集(本手被发牌、手尾才坐出者本手仍 `new_here=False`);**①.10 端到端**(坐出一手 → 回来 ready → 必付一个入局大盲)。
- **waive 优先**:既等大盲又在 waive 快照者走 core 免费正常入局(不被强塞大盲)。
- **短码 waiter 当大盲**:`points<BB` 投不满即 ALLIN + 守恒。
- **PART C wire 切片**:`SitDown(wait_for_big_blind=True)` → Seat 透传;缺省 → 付盲即玩。

`test_start_hand.py`:更新 `test_wait_for_big_blind_not_dealt` 注释(现为「非大盲位 → 不发牌」一侧,非「时机留 0011」)+ 文件头指向 test_wait_for_big_blind。

## 自 review(push 前对抗式 7 维)

> push 前跑了一轮多 agent 对抗式 7 维复审(每维 finder × refute-by-default 双签 + 综合)。**候选 9、确认 5、驳回 4,最高 minor,零 major/正确性缺陷**——实现逻辑(筹码守恒 / 分层 / 算法)全部通过对抗复核;阻挡提交的只有流程项(本篇正文回填)+ 两处测试加固。

按维度:

- **① 分层/不变量**:1 确认(`sweep_entrant` 在退化输入会 raise 而前置未声明)→ 已加前置注释(core_dealt 非空 + button∈core_dealt,调用方守);1 驳回。
- **筹码守恒(money 红线)**:0 确认 / 1 驳回——入局者是 `order[1]=大盲`、在 `blind_seats` 内,`_post_entry` 的 `not in blind_seats` 守卫天然排除它 ⇒ 只下结构大盲、不双重 post(`points==98` 测试钉);per-player `points+bet==in_game_points` 守恒。
- **④ 数据模型/算法**:0 确认 / 1 驳回——`blinds.py else None` 在 `button∈core_dealt` 契约下经穷举证明不可达(注释已陈述不变量);庄位定于 core_dealt(非并集)有反例支撑、测试钉。
- **②③ 文档同步/一致**:3 确认——(a)范围行漏列 `wire-protocol-guide.md` → 已补;(b)`wire-protocol-guide.md`/`core.md` 的 `sit_down`/`SitDown` 字段表漏 `wait_for_big_blind` → 已补两处;(c)`core.md` step1 括注只列 2/4 分支 → 已补全 4 分支。
- **⑤⑥ 规范/测试充分**:2 确认——(a)`test_two_waiters_only_sb_closest_enters` 首合取项 `seat_position==2` 恒真无效 → 删,换 `bet_amount==BB`;(b)tie-break `min(qualifiers)` 变异体全绿(原多候选测试里 `min` 恰等 `order[1]`)→ 新增 `test_two_waiters_entrant_is_big_blind_not_smallest_seat`(core={0}/waiters={1,2}/button=0 → 入局者座2≠最小座1)杀变异;1 驳回(同 `else None`)。

确认项均已当场修(代码/测试/文档),修后全量 **229 绿** + codegen `--check` 无漂移 + core 纯度 grep 通过(无 shell/async/IO import)。

## 待办 / 下一步

- 坐出再回想「等大盲」目前无法重新声明(默认付盲即玩)——若需,后续给 `SetUserStatus` 或专门命令带 `wait_for_big_blind`。
- `SetSmallBlind`/`SetBuyIn`(0 号位配置,P8)。
