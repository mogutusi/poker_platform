# 0020 · P1 余项:免盲投票(rules.md ①.12-15)

日期:2026-06-23 · 范围:`app/core/reduce.py`(+`_open_free_entry_vote`/`_vote_free_entry` + 投票结算/重算 helper + 离场/状态变更挂钩)、`app/wire/server.py`(+2 投票态报文)、`app/wire/client.py`(+2 投票报文 + `to_command`)、`scripts/gen_wire_ts.py` 无改(注册表驱动)+ 重生成 `frontend/src/types/wire.gen.ts`、`tests/core/test_free_entry_vote.py`(新)、`tests/wire/test_protocol.py`(补样本)、文档(`wire-protocol-guide.md`/`rules.md`/`core.md`/`TODO.md`)。承 [0016](0016-replan-wire-first.md) 重排表「P1 余项」首项。**纯 core 规则 + wire 切片,完成 block ① 入局规则的免盲投票那半。**

## 背景 / 为什么选这一项(批判性思考,README §0)

执行序到「P1 余项」,首项是免盲投票。核查发现其基础设施**已全部预置**:命令 `OpenFreeEntryVote`/`VoteFreeEntry`(commands.py)、域 `EntryVote(approvals/rejected)` + `Room.entry_vote`/`Room.waive_entry_for`(domain.py)、错误码 `NO_VOTE_IN_PROGRESS`/`NOT_A_VOTER`(errors.py)、且 `_start_hand`/`_eligible_seats` **已消费** `waive_entry_for`(0010)。本批只需补「产出 `waive_entry_for`」那一端 + wire 切片。

为什么是它而非「等大盲再入局时机」(①.7-10)或「JoinRoom+StateSnapshot」:① 它最自包含(命令/域/错误码/消费端全已就位,纯增量);② 属 block ①(最高积分风险面,架构要求先钉死)且完全可纯单测;③ 不依赖等大盲的 `new_here` 维护修复(投票只**读** `new_here`,二者正交)。等大盲(①.7-10)与 JoinRoom 留作随后单元。

## 关键设计决策(批判性 + 与文档对齐)

1. **投票人(voter)= 已入局(非 `new_here`)且 `READY_TO_PLAY` 的座位**(rules.md ① 行 69)。每个结算点**实时重算**(`_voters(room)`),不缓存——这让「投票人离场/坐出后重算」(①.15)自然成立。`new_here`/观战/`SITTING_OUT`/`OFFLINE`/`PLAYING` 都不投票。

2. **候选(candidate)= 当前 `new_here` 座位**(`_free_entry_candidates`)。通过时**快照**当前候选并入 `room.waive_entry_for`(union,不覆盖既有快照)。快照即「通过那一刻的 `new_here` 集合」——天然实现 ①.14「投票通过后才坐下的新玩家不在快照里」(他坐下时快照已定)。

3. **真空通过守门(防 bug)**:`_finish_entry_vote` 通过判据是 `voters 非空 且 voters ⊆ approvals`。**必须带「`voters` 非空」**——否则投票人集合为空时 `∅ ⊆ approvals` 真空为真会瞬间「全票通过」免掉所有人,等价绕过盲注结构。bootstrap(开桌第一手)本就免费、无需投票(rules.md ① 行 77),这条守门也兜住「无合格投票人时不会误免」。

4. **`reject` 即时清空(用 `EntryVote.rejected`)**:任一 `reject` → 置 `vote.rejected=True` → `_finish_entry_vote` 见 rejected 即清空 `entry_vote`、产 `FreeEntryVoteClosed(passed=False)`(rules.md ① 行 73)。用域里已有的 `rejected` 字段(0002 预留),不另立机制。

5. **`OpenFreeEntryVote` 门槛**:`origin` 在房;**无候选或无投票人 → `Err(CANNOT_OPEN_VOTE)`**(防开一个永远悬挂/无意义的投票,兼挡「无投票人真空通过」);**已有进行中投票 → 幂等 no-op `([],None)`**(不重置 approvals,防反复开票刷掉已有赞成的「重置刷票」)。开票成功产 `FreeEntryVoteUpdated(candidates,voters,approvals=())`。新增错误码 `CANNOT_OPEN_VOTE`。开票者**不必是投票人**(可由新人自己请求、或老玩家发起);身份只需在房。

6. **投票人集合缩小 → 重算(①.15)**:在**离场**(`_begin_leave` 立即驱逐分支,覆盖 LeaveRoom + Cleanup)与**就座内状态变更**(`_set_user_status` 非 PLAYING 路径,如 voter 坐出/起身)后挂 `_maybe_resolve_entry_vote`——仅当因此达成「全票非空」才通过(产 Closed),**否则不产事件**(不刷无谓进度)。**不挂 `_disconnect`**:`_voters` 每次实时重算已保证投票人集合永远新鲜,断线者(OFFLINE≠READY_TO_PLAY)在下一个结算点自然不计;不为断线单独触发「惊讶免费通过」。这与 ①.15 行为一致、范围聚焦(README 范围聚焦)。

7. **wire 两报文(出站),按事件语义分判别量**(wire.md 形状 #1):
   - `FreeEntryVoteUpdated{candidates,voters,approvals}`:开票 + 每次非终结 approve 的**当前态**(开票即 approvals=())。
   - `FreeEntryVoteClosed{passed,waived}`:终结(通过带快照 `waived`,否决/失败 `waived=()`)。
   字段皆 `tuple[str,...]`(→ TS `string[]`)/`bool`,无底牌/牌堆(结构性隐私天然满足)。投票广播到全房(观战者也看得到投票态,符合公开信息)。client 两报文 `OpenFreeEntryVote{}`/`VoteFreeEntry{approve}`,身份不进报文、`to_command` 平凡映射。

8. **不引入新枚举/值对象**:报文字段只用 `str`/`bool`/`tuple`,codegen 的 `_ENUM_ORDER`/`_VALUE_OBJECT_ORDER` 无须改;注册表加 4 个消息即可,生成器自发现。

## 打算改什么(开工前)

- `app/core/errors.py`:+ `CANNOT_OPEN_VOTE`。
- `app/wire/server.py`:+ `FreeEntryVoteUpdated`/`FreeEntryVoteClosed` + 注册进 `SERVER_MESSAGES`。
- `app/wire/client.py`:+ `OpenFreeEntryVote`/`VoteFreeEntry` + 注册进 `CLIENT_MESSAGES`/联合 + `to_command` 两臂。
- `app/core/reduce.py`:reduce match 加两臂;`_open_free_entry_vote`/`_vote_free_entry`;helper `_voters`/`_free_entry_candidates`/`_finish_entry_vote`/`_maybe_resolve_entry_vote`;`_begin_leave`(立即驱逐分支)与 `_set_user_status`(非 PLAYING 路径)末尾挂 `_maybe_resolve_entry_vote`。
- `scripts/gen_wire_ts.py`:无改(注册表驱动);重生成 `frontend/src/types/wire.gen.ts`。
- `tests/core/test_free_entry_vote.py`(新):①.12-15 + 开票/投票错误臂 + 进度报文 + 守恒(投票不动积分/座位)。
- `tests/wire/test_protocol.py`:`_broadcast_samples` 加 2 报文(隐私序列化);parse/to_command + registry 覆盖加 2 client 报文。
- 文档:`wire-protocol-guide.md`(投票报文移到「已交付」)、`rules.md ①`(钉死开票门槛/真空守门/重算触发的实现决策)、`core.md`(事件一览补投票行)、`TODO.md`(勾免盲投票 + 更新 blinds.py `[~]` 注)。

## 实际改了什么

- **`app/core/errors.py`**:+ `CANNOT_OPEN_VOTE`。
- **`app/wire/server.py`**:+ `FreeEntryVoteUpdated{candidates,voters,approvals}` / `FreeEntryVoteClosed{passed,waived}`,注册进 `SERVER_MESSAGES`。
- **`app/wire/client.py`**:+ `OpenFreeEntryVote{}` / `VoteFreeEntry{approve}`,注册进 `CLIENT_MESSAGES` + 联合 + `to_command` 两臂。
- **`app/core/reduce.py`**:reduce `match` 加 `OpenFreeEntryVote`/`VoteFreeEntry` 两臂;新 `_open_free_entry_vote`/`_vote_free_entry`;helper `_voters`/`_free_entry_candidates`/`_finish_entry_vote`(终结判定,不产进度)/`_maybe_resolve_entry_vote`(投票人缩小后静默重算);`_begin_leave` 立即驱逐分支 + `_set_user_status` 非 PLAYING 路径末尾挂 `_maybe_resolve_entry_vote`(①.15)。用域 `EntryVote.rejected`。
- **`frontend/src/types/wire.gen.ts`**:重生成(+4 接口 + 两联合新增成员;字段 `string[]`/`boolean`,无新 enum/值对象)。
- **`tests/core/test_free_entry_vote.py`(新)**:12 测试——①.12 全票免盲 + 下手免费入局、①.13 一票否决 + 下手付盲即玩、①.14 蹭车快照、①.15 离场重算 + 坐出重算变体、候选自开票、开票/投票错误臂(NOT_IN_ROOM/CANNOT_OPEN_VOTE 无候选/无投票人/NO_VOTE_IN_PROGRESS/NOT_A_VOTER)、幂等开票、投票不落库/不动座位。
- **`tests/wire/test_protocol.py`**:`_broadcast_samples` + 2 投票报文(隐私序列化);parse/to_command + registry 覆盖 + 2 client 报文。
- **文档**:`wire-protocol-guide.md`(§3/§4 投票报文 + §8 移到已交付)、`rules.md ①`(实现细节:开票门槛/真空守门/重算触发/wire)、`core.md`(事件一览补免盲投票行)、`TODO.md`(勾免盲投票 + 更新 reduce/blinds/tests 状态行,共 190 测试)。

**偏离计划**:范围与「打算」一致。`scripts/gen_wire_ts.py` 如预期无须改(注册表驱动,生成器自发现新消息;字段仅 str/bool/tuple,无新 enum/值对象)。`test_protocol.py` 的 registry 覆盖测试如预期因新增 2 client 报文先红、补样本后绿(防漏测机制生效)。

### 自 review 后增补(候选侧失效,修复对抗 review 发现的 root cause)

初版把 `approvals` 当成「对免盲投票本身」的同意,未绑定到具体候选集——对抗式 review(见下「自 review」)抓到一条 **major 不变量缺陷 + 多条同根**:`approvals` 在候选集合改变时从不失效,导致 ① 原候选离场后孤儿票残留、新候选入座用陈旧 `approvals` 被误免(绕过盲注结构/防躲盲);② 中途坐下的新人被并入 `waive`;③ 无候选时仍判 `passed=True`;④ 残票跨 `StartHand` 悬挂。**根因一处、修复内聚**:

- **`app/core/domain.py`**:`EntryVote` 加 `candidates: frozenset[str]`(开票时冻结的 new_here 候选;必填,排首位)。
- **`app/core/reduce.py`**:`_open_free_entry_vote` 用 `EntryVote(candidates=frozenset(candidates))` 冻结候选;`_finish_entry_vote` 改 `waived = vote.candidates ∩ 当前 new_here`,且**候选非空守门**(空 → 失败清空,与开票门槛对称),通过只免「冻结候选中仍在的」;`_vote_free_entry` 进度的 `candidates` 取冻结集 ∩ 当前 new_here;`_start_hand` 清 `waive_entry_for` 处一并 `room.entry_vote = None`(残票随开局作废)。
- **tests**:`test_free_entry_vote.py` +6(候选离场孤儿票失效不复用、中途坐下不蹭车、残票随开局作废、进度剔除离场赞成、多候选排序、坐出非投票人);`test_domain.py` 的 `EntryVote()` 改带 `candidates`。共 **196 全绿**。
- **docs**:`rules.md ①`「实现细节(0020)」补候选侧失效语义(冻结/中途不蹭/原候选离场票失对象/开局作废);`error.md` ErrorCode 示意清单补 `CANNOT_OPEN_VOTE`。

## 自 review

方法:按 [review.md](../../review.md) 7 维跑**对抗式 review 工作流**(7 维各派独立审查者 → 每条候选发现再派独立反驳者「默认先试图反驳」,驳不倒才算)。结果:**13 候选 / 10 存活 / 3 驳回**;存活项已全部当场修复(代码 + 测试 + 文档)。这是「测试全绿仍被 review 抓到」的又一实证——绿测覆盖「我想到的」,review 覆盖「我没想到的」(本次抓到的是候选侧失效的不变量缺陷,初版 12 测全绿却漏)。

**存活并已修(同根聚合)**:

- **[major · 分层/账本] 陈旧投票跨候选变更未失效 → 旧赞成免掉无关新候选(绕过盲注/防躲盲)**:`approvals` 未绑定候选集。修:`EntryVote.candidates` 开票冻结 + `_finish_entry_vote` 取 `vote.candidates ∩ 当前 new_here` + `_start_hand` 清 `entry_vote`。**钱/公平相关,最高优先**,已修 + 补测(`test_departed_candidate_orphan_vote_cleared_no_freeride`/`test_mid_vote_joiner_not_waived`)。
- **[minor ×2] 无候选时仍 `passed=True`(与开票门槛不对称)**:`_finish_entry_vote` 加候选非空守门(空 → 失败清空)。已修(并由上面孤儿票测试覆盖 `passed=False`)。
- **[major+minor ×2 · 测试] 「投票未完即 StartHand」非阻塞路径无测试 + 残票悬挂**:`_start_hand` 清 `entry_vote` + 补 `test_pending_vote_discarded_on_start_hand`(钉 D 付盲即玩 + 残票作废)。已修。
- **[minor · 测试] 进度剔除离场赞成分支(`approvals & voters`)未触发**:补 `test_progress_prunes_departed_voter_approval`(4 投票人,赞成者离场后进度剔除)。已修。
- **[nit · 测试] 多候选 waive 排序 / 坐出者非投票人 阈值未测**:补 `test_multi_candidate_waive_sorted` / `test_sitting_out_established_not_a_voter`。已修。
- **[nit · 文档] error.md ErrorCode 示意清单漏 `CANNOT_OPEN_VOTE`**:已补。

**驳回(3,核实后不成立)**:① `CANNOT_OPEN_VOTE` 合并「无候选/无投票人」两因——是 rules.md 行 81 + 决策 5 明确选定、`detail` 区分、对玩家语义同一,纯设计取舍非缺陷;② rules.md 行 83「投票人恒 READY」措辞——`_voters` 已用 READY_TO_PLAY 兜,无歧义;③ `test_late_joiner` 可选增强(同手内 E 跑 StartHand)——非缺陷,且 `test_mid_vote_joiner_not_waived` 已覆盖更强的中途坐下场景。

**逐维结论(修复后)**:

- **① 分层 / 不变量**:`grep app/core` 无 fastapi/sqlalchemy/sqlmodel/websockets/app.shell;投票簇纯同步、helper 不 raise(Go 风格 `list[Event] | None`);只改工作副本(`room.entry_vote`/`waive_entry_for` 在深拷的 `work.room`);事件字段 `tuple[str,...]`/`bool` 快照值,不持域活引用。**核心红线**:投票全程不动 `UserState.points`/`Seat.points`/`hole_cards`(守恒/隐私不变量 2/3)——测试 `test_unanimous...` 断言座位筹码不变、无 `Persist`;`waive` 只改 `room.waive_entry_for`,真正免付在 `_start_hand` 的 `_eligible_seats`(0010 既有路径,本批未破坏)。
- **② 代码↔文档同步**:候选冻结/真空双守门/重算触发/开局作废/wire 报文均已落 `rules.md ①`「实现细节(0020)」;`CANNOT_OPEN_VOTE` 落 `error.md`;`core.md` 事件一览补免盲投票行;`wire-protocol-guide` §3/§4/§8 补投票报文。
- **③ 文档↔文档一致**:测试计数 196 已同步 TODO 两处状态行;`rules.md`/`core.md`/`guide` 新增内容与代码一致;链接指向真实路径。
- **④ 数据模型正确性**:`EntryVote.candidates` 用 `frozenset`(冻结语义,排序无关,必填防误构造);`rejected` 经域字段表达即时否决;开票门槛/幂等 no-op 不让不可能态可表达;无候选守门避免「通过却无对象」的矛盾态。
- **⑤ 规范合规**:新增字段/码带中文注释;命名表意;无裸魔法数 / 死代码;`Err.detail` 带可定位上下文(数量);反直觉处(真空守门、候选冻结、幂等开票、开局作废)均有「为什么」注释;`reduce.py` 文件头 docstring 更新(原「(0010)只落地 StartHand」已纠)。
- **⑥ 测试充分**:18 测试覆盖 ①.12-15 全部 + 候选冻结 4 个对抗场景 + 开票/投票 5 错误臂 + 真空守门 + 幂等 + 进度剔除 + 排序 + 坐出界定;断言投票不落库/不动座位(守恒);wire 序列化隐私(`test_protocol` 双报文)。196 全绿。
- **⑦ 流程账本**:打算↔实际差异(候选侧失效修复)已记本段;TODO 勾项 + 计数同步;提交将引用 `0020`、全英文。对抗 review 工作流的存活/驳回已逐条入账。

> 批判性自评:本批最大价值不在初版功能,而在对抗 review 抓出的**候选侧失效**——它是「测试全绿、规则文档 ①.14 只防 after-pass、却漏 before-pass + 候选离场」的典型盲区。修复把「同意绑定到冻结候选集」这一正确语义钉死,并双向同步进 rules.md ①,后续等大盲/JoinRoom 不会再踩。

## 待办 / 下一步

- 等大盲再入局时机(①.7-10):`_start_hand` 中 BB 路过 `wait_for_big_blind` 座位免费入局 + 躲盲被堵(坐出/换座/退房再进算 new_here)。
- `JoinRoom`+`Connect`+`StateSnapshot`、`RoomChat`、`Set*Blind`(随配置收编)。
