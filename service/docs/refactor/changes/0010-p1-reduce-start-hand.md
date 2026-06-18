# 0010 · P1(三之一):reduce 骨架 + `_start_hand`(开局)

日期:2026-06-18 · 范围:`service/app/core/reduce.py`(新)、`service/app/core/messages.py`(新)、`service/app/core/errors.py`(+1 码)、`service/tests/builders.py`(+`run`/`make_table`/`DECK`)、`service/tests/core/test_start_hand.py`(新)、文档同步(core.md / rules.md / TODO)。

## 背景 / 打算改什么

P1 的核心是 `core/reduce.py`(见 [core.md](../../core.md) §reduce 结构 / 一手牌生命周期)。按 [README §0](../README.md) 先质疑「一篇做完整个 reduce」这个拆分:

- reduce 覆盖**全命令集**(开局/动作/推进/摊牌/结束/连接/断线/超时/清理/买入/入座/状态/聊天/投票),一篇做完 = 几百行 reduce + 几百行测试,且把**最易出钱错的结算路径**(边池)和**位次/入局**挤在一次,审查与正确性都吃亏。0008 当时把 blinds 拆成「定位」「入局/投票」两步正是同一智慧。
- 因此把 reduce **按命令簇分篇**落地。本篇(0010)只做**开局 `_start_hand`**——它本身是一个完整工作单元(rules.md ① 的「开局那半」:定位[0008 已做] + 入局资格 + 建手 + 下盲 + 发牌 + 置 PLAYING + 定行动者),且能脱离 DB/WS 纯单测(P1 的兑现点)。

**本篇范围(0010)**:

- `reduce(work, cmd)` 顶层 `match` 骨架:只实现 `StartHand`;其余命令暂以 `Err(INTERNAL)` 归一(工作副本被丢弃、world 不动),随后续变更逐个落地、`case _` 随之收缩。
- `_start_hand`:校验(PENDING_START、无在途 Hand、发起人在其座且 READY_TO_PLAY、在局 ready ≥2)→ **入局资格**(established / 付盲即玩 / bootstrap / 尊重 `waive_entry_for` 快照)→ 定位(复用 [blinds.py](../../../app/core/rules/blinds.py))→ 锁筹(Seat.points→Player.points + `in_game_points` 快照)→ 下盲([blinds.post_blinds](../../../app/core/rules/blinds.py))+ 入局 post → 发底牌(`StartHand.deck` 或 `deck.shuffled_deck()`)→ 置 PLAYING / HAND_STARTED → 定 preflop 首行动位(复用 [betting.next_active_position](../../../app/core/rules/betting.py))→ 产出事件。
- `core/messages.py`:reduce 投影域状态产出的**出站载荷**(`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerView`)。见下「事件载荷」决策。

**本篇不做(留后续,已在 TODO/本篇待办点名)**:

- `_player_action` + 街推进 + 摊牌 + 结束/分池/结算(边池兑现点)——下一篇 0011。
- **等大盲再入局时机**(BB 路过座位才入局,rules.md ①.7-①.10):本篇 `wait_for_big_blind` 的新玩家**本手不发牌**(正确的「等」行为),但「BB 推进到其座那手免费入局」的精确时机 + 躲盲被堵的几条用例留 0011。
- **免盲投票** `OpenFreeEntryVote`/`VoteFreeEntry`(rules.md ①.12-①.15):本篇只**消费** `room.waive_entry_for` 快照(测试预置该集合即可验「尊重快照免费入局」),投票 handler 产出该快照留 0011。
- rules.md ④ 局中 LeaveRoom/SITTING_OUT/断线;`_timeout`/`_cleanup`;lobby/连接簇。

## 设计决策(开工前定的)

1. **事件载荷 = 临时 core 侧 dataclass(非 wire Pydantic)**。reduce 是首个事件产出者,但 wire 是 P6、未落地。依 [models.md](../../models.md)(「core 构造 wire ServerMessage」「字段在 .py、随域模型定」)+ [wire.md](../../wire.md)(「具体消息字段未写/待定」),本篇在 `core/messages.py` 用**纯 frozen dataclass**(挂 [events.py](../../../app/core/events.py) 既有 `ServerMessage` 占位基类)承载语义快照,**只装 reduce 算得出的字段**。P6 wire 落地时由 Pydantic 可辨识联合 DTO 取代/对齐(投影点集中在 reduce,替换面可控)。这样保住「P1 无 wire/codegen 也能纯单测」,又不**过早冻结** wire 明确推迟的字段形状。隐私由构造保证:`HandStarted`/`HandStatusChanged`/`PlayerView` **无** `hole_cards`/`deck` 字段,底牌只在 `Personal(HoleCards)`。
2. **入局 post 算 live**(rules.md ① 推荐):付盲即玩者投一个 BB 进 `bet_amount`(短码 all-in),`has_acted=False`(轮到时可 check/raise)。**占盲位的入局者**(序中 SB/BB 位)以结构盲注充作入局付费、不重复 post(SB 位入局者因此本手只付 SB——属 rules.md ① 明示「不做精确死/活盲记账」的可接受近似,已在 rules.md 注明)。
3. **每手必推进 button**:`_start_hand` 无条件 `advance_button`(含第一手);测试用例据初始 `button_position` 反推预期位次。简单且公平,不为「第一手不动庄」加特例。
4. **发牌不烧牌(no burn)**:底牌取洗后牌堆前 `2N` 张(轮转:玩家 j 取 `deck[j]`、`deck[N+j]`),余下存 `hand.deck` 供后续街(0011 发公共牌)。本规模不引入烧牌。
5. **发起人未 ready** 新增 `ErrorCode.NOT_READY`(现有码无贴切项;`ILLEGAL_ACTION` 限下注规则、语义不符)。

## 接缝校验(复用,不重造)

- 定位:`blinds.advance_button(button, dealt)` + `blinds.seat_order(button, dealt)`(`dealt` = 本手在局座位下标集合,正是 0008 设计的「eligible 入参」)。
- 下盲:`blinds.post_blinds(hand, small_blind)`。
- preflop 首行动:`betting.next_active_position(hand, 1)`(BB 下一位;heads-up 自然回到 players[0]=button/SB——一个公式覆盖两种)。

## 实际改了什么

新增:

- `app/core/reduce.py`:`reduce` 顶层 `match`(只实现 `StartHand`,`case _` 暂 `Err(INTERNAL)` 兜底)+ `_start_hand` + `_eligible_seats`(established/付盲即玩/bootstrap/waive)+ `_post_entry`(入局 live post)+ `_start_hand_events`(投影出站载荷)。
- `app/core/messages.py`:`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerView`(临时 frozen dataclass,见设计决策 1)。
- `app/core/errors.py`:+`ErrorCode.NOT_READY`。
- `tests/builders.py`:+`run`(checkout→reduce→commit/discard 单步驱动)、`make_table`(拼已就座的桌)、`DECK`(固定牌堆);`seat()` +`wait_for_big_blind` 形参。
- `tests/core/test_start_hand.py`:18 测试。

**计划外的依赖方向修正(按 [README §0](../README.md) 当场修 + 记)**:写 reduce 时发现 `Work` 定义在 `shell/world.py`,而 `reduce`(core)需 `Work` 作入参类型 → 触发「core import shell」违反铁律(P0 时 reduce 未建,违反未显形)。**把 `Work` 的类型定义上移到 `core/domain.py`**(它是 reduce 的操作面);`checkout`/`commit` 仍在 `shell/world.py`、改从 `core.domain` import `Work`(shell→core 合法)。已同步 [storage.md](../../storage.md)。core 纯度复验:`grep` 确认 `app/core/` 无 shell/fastapi/sqlalchemy/websocket/asyncio import。

文档同步:`storage.md`(Work 类型位置)、`rules.md` ①(入局者占盲位简化)、`models.md` 待定(core/messages.py 临时载荷)、`core.md` §1.5(不烧牌)、`TODO.md`(reduce/blinds/tests 进度)。

## 测试(rules.md ① 子集,经 reduce 集成)

22 测试,**全量 88 绿**(0009 的 66 + 本篇 22)。对应编号:

- 定位经 reduce:①.1 三人(含「已入局非盲位玩家免付」断言)、①.2 六人(preflop 首行动;postflop 留 0011)、①.3 heads-up、①.4 庄推进跳过 SITTING_OUT、①.5 短码盲注 all-in。
- 入局资格:①.6 付盲即玩(post 一个 BB live、清 new_here、立刻 PLAYING)、①.11 bootstrap(2 人 + **可分辨的 3 人版**:非盲位玩家也免付)、防躲盲(唯一已入局玩家坐出仍堵掉新人免费入局)、尊重 waive 快照(①.12 前置:免费入局 + 清快照)、等大盲 0010 行为(本手不发牌、未锁筹、仍 ready)。
- 机制:锁筹(Seat.points→Player + in_game_points 快照 + 清零)、**逐玩家守恒**(`points+bet_amount==in_game_points`、contributed 空)、发牌(轮转/不烧牌/余牌存 hand.deck)、**事件顺序契约**(HandStarted→HoleCards*→HandStatusChanged→TurnChanged 殿后)+ **隐私**(广播无底牌字段、无 deck 泄露、每人一条 Personal(HoleCards))、状态机(HAND_STARTED + PLAYING)、**全员投盲 all-in → acting_position=None 不卡 raise**。
- 错误臂(失败丢工作副本、world 不动):HAND_IN_PROGRESS、NOT_READY、NOT_ENOUGH_PLAYERS、NOT_YOUR_SEAT、**注入牌堆过短 → INTERNAL(不 raise)**。

## 自 review(push 前多 agent 对抗式:6 维 finder × 每条 refute-by-default 核实)

**18 条候选、15 确认、3 驳回,零 critical/major**——开局核心(资格/定位/锁筹/下盲/发牌/守恒/隐私)经对抗核实仍稳。确认项按真实严重度修复 / 记录:

确认并已修(代码):
1. **bootstrap 判据漏算坐出的已入局玩家(minor,真实规范偏差)**:原 `bootstrap` 只在 `ready` 子集上算,唯一已入局玩家若 SITTING_OUT(不在 ready)会被误判为 bootstrap、放新人免费入局(破防躲盲,rules.md ① 行 46/50)。改为看**整桌已占座位** `not any(s is not None and not s.new_here for s in room.seats)`,与 L113 注释 + 行 60 规范一致。补 `test_sitting_out_established_blocks_free_entry`(变异测试验证:旧码下该测试失败,load-bearing)。
2. **注入牌堆过短抛 IndexError(nit,违反 helper「绝不 raise」)**:发牌前加长度校验,过短返 `Err(INTERNAL)`;校验置于任何 mutation 之前(先校验后改)。补 `test_err_injected_deck_too_short`。

确认并已修(测试/文档卫生):
3. **false-green:bootstrap / established-免付 分支不可分辨**:原 bootstrap 测试 2 人均占盲位,`bootstrap=False` 变异也绿;补可分辨的 3 人 bootstrap 测试 + 在 ①.1 加「已入局非盲位玩家 bet==0」断言。
4. **事件顺序未真正断言**:原测试只钉 `events[0]`;补「HoleCards 夹在 HandStarted 与 HandStatusChanged 间、TurnChanged 殿后」的顺序断言。
5. **变更记录文档过度声称**:①.2 实测只有 preflop 首行动,删「含 postflop 首行动断言」;泄漏的 `</content>`/`</invoke>` 收尾标签删除。

确认但记录为 0011 待办(非 0010 缺陷):
6. **全员投盲即 all-in → born-all-all-in 手**:0010 正确产出 `acting_position=None`、不产 TurnChanged(符合 nullable 字段语义),但「跑公共牌直接摊牌」收尾属 0011。补 `test_all_dealt_all_in_on_blinds_no_actor` 钉住 0010 行为,提醒 0011 街推进入口须接住此 born-all-in 手(不能只靠动作驱动)。

驳回(3 条,均非缺陷,核实者给反例):`run` 驱动忠实镜像 GameLoop;`seat()` 新 kwarg 向后兼容;`contributed=={}`/`epoch==0` 断言虽属默认值但语义正确(留作开局不变量)。`wait_for_big_blind` 发起人可开一手自己不入局——核实为「符合 core.md 字面 gate」的良性怪癖,不改(待状态/座位 handler 落地再议)。

## 待办 / 下一步

- 0011:`_player_action` + 街推进 + 摊牌 + 结束/分池/结算(rules.md ②③ 经 reduce;守恒/隐私断言;单人直接结束 / 全 all-in 跑公共牌 / 摊牌揭示)。**街推进入口须接住「born-all-in 手」**(开局即全员 all-in、`acting_position=None`、无动作会到来):不能只靠 `PlayerAction` 驱动,`_start_hand` 后若无 ACTIVE 应直接进跑公共牌→摊牌(见自 review §6 + `test_all_dealt_all_in_on_blinds_no_actor`)。
- 等大盲再入局时机 + 躲盲被堵(①.7-①.10)、免盲投票(①.12-①.15)。
- reduce `case _` 随各 handler 落地收缩;全部落地后该兜底分支应不可达。
- (工程卫生)pre-commit 加一条 `grep` 拦截 `docs/` 下泄漏的 `</invoke>`/`</content>`/`<parameter` 等生成器标签(本篇 0010 曾漏入变更记录,见自 review §5)。
