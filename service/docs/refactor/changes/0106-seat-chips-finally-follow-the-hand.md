# 0106 · 座位筹码终于跟着牌局走(BUG-20)

日期:2026-08-28 · 性质:**缺陷修复(协议 + 前端)**· 触发:[BUGS.md](../BUGS.md) BUG-20 —— 0105 复审实测出「座位卡片上的筹码从入座那一刻起就不再变」:整手牌里下注、跟注、赢池,那个数字全程 100/100,而结算面板同时写着「你赢了 4」。

## 缺陷的两半(登记时已查清,这里只复述判据)

- **在手那半(前端投影错位)**:`player_acted` / `hand_status_changed` 带来的实时筹码进了 `state.players`,而座位卡片读的是 `state.seats[].points`——后者只有 `state_snapshot` 与 `player_bought_in` 会更新。服务器自己的快照投影规则是「**在手取 `Player.points`,不在手取 `Seat.points`**」(合并规则在 `reduce.py` `_state_snapshot`;「筹码后手」这句注释在 `app/wire/server.py` 的 `SeatView.points` 上),前端照抄这条口径即可,不需要动协议。
- **结算那半(wire 上没有承载)**:`_finalize_hand` 把 `Player.points` 还回 `Seat.points` 之后,广播出去的只有 `UserStatusChanged`,它不带筹码。结算后的座位筹码前端够不着;自己拿 winnings/refunds 去加等于复算结算的记账,前端不变量 1 禁止。

## 打算怎么改

### 结算那半:`HandEnded` 加 `stacks`

新 DTO `SeatStack(seat_position, nickname, points)`,`HandEnded` 加字段 `stacks: tuple[SeatStack, ...]` —— **本手每个参与者结算后的座位筹码**。

为什么挂在 `HandEnded` 而不是 `UserStatusChanged`:

1. **一个产出点**。`HandEnded` 只在 `_finalize_hand` 一处构造,而那里的 participants 循环**本来就在算这个数**(`ParticipantWrite.final_points = s.points` 还回之后)——wire 字段与手牌记录同源同循环,不会漂。`UserStatusChanged` 有六七个产出点(入座/ready/坐出/断线/重连/手尾/重标),每处都得去解析一份座位筹码。
2. **手尾的 `UserStatusChanged` 覆盖不全**。`settled_status` 对本手离桌者是 `continue` 跳过的(他们随后走 `_evict`),而离桌者恰恰也参与了结算;`stacks` 按参与者集合给,连他们一起带上(客户端更新后紧跟的 `UserLeft` 会把那个座位移除,顺序无害)。
3. 语义就是「这手怎么结的」的一部分:钱回到了每个座位多少。

隐私核对:座位筹码本来就是公开广播面(`SeatView.points`、`PlayerBoughtIn.seat_points`),无新增暴露。

### 在手那半:前端投影照抄服务器口径

`game/page.tsx` 的座位投影改为:**手牌进行中且此人在手 → 显示 `state.players` 里他的实时筹码;否则显示 `state.seats[].points`**。判据 `gameStarted`,与服务器 `in_hand` 的语义一致;结算展示期(0105)里 `handStatus` 已是 `null`,所以显示的是同批 `stacks` 刚写进 seats 的结算值,而不是 `players[]` 里结算前的残值。

store 的 `hand_ended` 臂把 `stacks` 写进 `seats[].points`,**按昵称匹配**(0105 的教训:座位号跨手会易主;虽然本条是同批到达、两个键此刻必然一致,仍统一用昵称,不留第二种索引习惯)。

### 顺带修正登记里的一个不精确

BUG-20「要补的测试」写的是「赢家的座位增加了他赢到的数」——**相对开局前的座位值这是错的**:结算值 = 下注扣完后的 `Player.points` + 分到的 payout,不等于开局值 + winnings(差了他投进池子的部分)。测试断言按真关系写:`结算值 == 河牌时显示值 + winnings + refunds`(全部是服务器给的数,测试做加法不算前端复算)。

### 要动的面(预期)

- `app/wire/server.py`:`SeatStack` + `HandEnded.stacks`;`app/core/reduce.py` `_finalize_hand` 填充;`tests/wire/test_protocol.py` 样本补字段;codegen 重生成 `wire.gen.ts`(`test_codegen_uptodate` 守门)。
- 后端测试:`test_player_action.py` 摊牌与无摊牌两臂断言 `stacks` 与还座后的 `Seat.points` 一致;离桌参与者也在 `stacks` 里。
- 前端:`store/room.ts` `hand_ended` 臂 + `room.test.ts`;`game/page.tsx` 座位投影 + 注释指回 `reduce.py` 的口径。
- e2e:`showdown.spec.ts` 补两条断言——河牌时在手座位的 `data-seat-points` **小于买入额**(盲注已扣,钉在手投影);手尾 `结算值 == 河牌值 + winnings(+refunds)`(钉结算链)。
- 文档:[BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(消息表 + 「别自己推」清单加一条「座位筹码别自己加减」)、[wire-protocol-guide.md](../../wire-protocol-guide.md) §3 `hand_ended` 行、[frontend/docs/state.md](../../../../frontend/docs/state.md) `HandEnded` 行;[BUGS.md](../BUGS.md) 划掉 BUG-20(含上面那处修正)。
- 后端行为变了 ⇒ **重启 uvicorn 并按房规验证重启生效**,再跑前端各层。

### 有意不做

- **不给 `StateSnapshot`/`SeatView` 动任何东西**:快照本来就带合并后的值,一直是对的。
- **不动买入/Ready 的门**(`me.points === 0`):它们读的是座位(结算)值,语义正确;本批修好 seats 的更新后,「打光筹码的人看不到买入入口」自然消解。是否真消解,验证段实测。
- **BUG-21(dev 账号积分只出不进)不混进来**:测试基建问题,另一批。

## 实际改了什么

按计划落地,无结构性偏离;两处比计划多的都在测试面。

### 后端(协议)

- **`app/wire/server.py`**:`SeatStack`(三字段各带含义注释)+ `HandEnded.stacks`(注释写明为什么必须由服务器给、为什么含离桌者)。
- **`app/core/reduce.py`** `_finalize_hand`:participants 循环里同步收集 `stacks`——**与 `ParticipantWrite.final_points` 同循环同源**,`s.points += p.points` 之后、驱逐之前取值;`SeatStack` 是冻结 DTO 装 int,后续 `_evict` 改座位不会波及已产出的事件(不变量 7)。
- **`scripts/gen_wire_ts.py`**:`SeatStack` 登记进 `_VALUE_OBJECT_ORDER`(生成器的「引用了但未登记」断言先红了一次,守门起效);重生成 `wire.gen.ts`,`--check` 与 `test_codegen_uptodate` 复验同步。
- **`tests/wire/test_protocol.py`**:样本消息补 `stacks`(必填字段,不补构造就报错——这正是要它必填的原因)。

### 后端(测试)

三条既有结算测试各补 `stacks` 断言,盯的都是**派生关系**:

- `test_showdown_single_pot_high_hand_wins`:`stacks` 与 commit 后的 `room.seats` 逐座相等 + **len 单独钉一遍**(dict 比较会折叠重复条目,「每个参与者恰好一条」得另说一句——这条是给一个自查出的存活变异补的,见验证)。
- `test_uncalled_bet_refunded_no_showdown`:无摊牌收尾也带 `stacks`(字段跟 `HandEnded` 走,不跟 `HandShowDown` 走)。
- `test_leave_allin_player_can_still_win_then_evicted`:**本手离桌者也在 `stacks` 里**,值等于退回全局的那笔 `PointsWrite`——他的座位此刻已释放,断言只能对着钱路。

### 前端

- **`store/room.ts`** `hand_ended` 臂:`stacks` 按**昵称**写进 `seats[].points`;不在 seats 里的条目(离桌者)安静跳过,不造座位。
- **`app/game/page.tsx`** 座位投影:`inHand.get(nick) ?? seatView.points`,判据 `gameStarted`——照抄服务器 `_state_snapshot` 的合并口径,注释指回出处。重连中途拿到快照不会双重合并:快照的 seats 已是合并值、players 是同一份实时值,这里的合并是**选择**不是减法,选哪边都是同一个数。
- **`store/room.test.ts`**:五个既有 `hand_ended` 夹具补必填的 `stacks: []`(类型系统逐个点名,这正是必填的价值);新增一条:stacks 写进座位、按昵称匹配、ghost 条目不凭空造座位。
- **`e2e/showdown.spec.ts`**:三组新断言——开局前记下买入值;河牌时 `data-seat-points` **小于**买入值(在手投影,修复前它纹丝不动);结算后 `座位值 == 河牌值 + 面板上分得的数`(三个数全是服务器给的,测试做加法不是前端复算)。`settlementByNick` 按「标着本手结算的 role=status」定位——页面上还有别的 `role="status"`(连接横幅、投票结果),只按 role 取第一个是在赌它们不在场(自查加固)。

### 文档

[BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)「最容易理解错的语义」新增「座位筹码别自己记账」一条 + 消息流里 `hand_ended` 括号补 `stacks`;[wire-protocol-guide.md](../../wire-protocol-guide.md) §3 行 + §5 时序示例;[frontend/docs/state.md](../../../../frontend/docs/state.md) `PlayerActed` / `HandEnded` 两行。`core.md` 只讲事件顺序、不枚举字段(「消息清单在 .py」),核过无需改。[BUGS.md](../BUGS.md) 划掉 BUG-20 + 「已修复」表补行 + **修正登记里那句不精确的测试建议**(「赢家座位 + winnings」相对开局前的值不成立);[TODO.md](../TODO.md) 0105 那行的指针补「0106 已修」。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **776 passed**(775 → 776:复审后补的旁观占座测试;其余新断言都补进既有测试——变异验证一律按**测试名**确认,不看计数) |
| codegen | `gen_wire_ts.py --check` 通过;`test_codegen_uptodate` 绿(改 .py 未重生成时它先红过一次,守门起效) |
| 前端 vitest | **96 passed**(95 → 96) |
| 前端 `tsc --noEmit` | 通过——五个既有 `hand_ended` 夹具被必填的 `stacks` 逐个点名,这正是把它设成必填的价值 |
| 浏览器 `npm run test:e2e` | **20 passed** |
| 三条冒烟 | 全部通过(smoke 脚本只读 `winnings`/`refunds`,加字段不破;`smoke:raise` 的边池结算在新 wire 上真跑过) |
| uvicorn | 改后端后按房规重启:杀 pid → 确认进程没了且端口释放 → 起新进程(pid 163275)→ grep 日志 `address already in use` **0 条** |

**肉眼确认**(1440×900,两张截图):河牌时 bob 的座位显示 **98**(盲注扣掉了,修复前是纹丝不动的 100);结算后 bob **102**、alice **98**,结算面板同屏写着「bob +4」——`98 + 4 = 102`,一屏对上账。

### 反向变异验证 7 处

| 变异 | 变红的 |
|---|---|
| 服务端 `stacks` 发空 | `test_showdown_single_pot_high_hand_wins` + `test_uncalled_bet_refunded_no_showdown` + `test_leave_allin_player_can_still_win_then_evicted`(按名确认) |
| store 忽略 `stacks` | vitest `hand_ended 的 stacks 写进座位筹码:按昵称匹配、非参与者不动、打光就是 0`;e2e 也红(`Expected 102, Received 100`)——**但见下「chop 会漏杀」** |
| 页面在手投影退回只读 seats | e2e 河牌那条:`Expected < 100, Received 100` |
| `stacks` 发重复条目 | `test_showdown_single_pot_high_hand_wins` 的 len 断言——**这条变异是自查出来的**:dict/Map 比较会把重复条目折叠掉,先补断言再验证它真能红 |
| store 换成按 `seat_position` 匹配 | vitest 同一条(alice 的条目故意填错座位号,换键就丢) |
| store 查不到就清零(`?? 0`) | vitest 同一条(carol 在座非参与者,800 必须纹丝不动) |
| 服务端按「有人的座位」重建 stacks | `test_stacks_exclude_bystander_seat`(复审后补) |
| (0105 的既有回归照跑)`tableCardsVisible` 等 | 未重跑,由全套 20 绿兜底 |

**e2e 的结算断言在 chop(和牌)那一手会漏杀变异,实测撞上过**:store 忽略 `stacks` 的第一次变异跑居然绿了——那一手恰好平分,每人分回自己那 2,`100 == 98 + 2` 碰巧成立;再跑三次全红(`102 vs 100` / `98 vs 100`)。**确定性的杀由 vitest 那条承担**,e2e 管的是「整条管道真的通」;如实记,不假装 e2e 单独就够。

### 复审后补的测试与修正

对抗复审(并行多 agent,四组维度 → 逐条独立证伪)确认了 8 条、驳回 2 条(驳回的两条正是我复审进行中已自查补上的:重复条目变异、旁观占座变异——它们的测试在复审读到的 diff 快照之后落地)。确认项全部当场修:

- **(最重)store 的「不在 stacks 里的座位分毫不动」分支没有测试钉**,而它恰恰是这个分支存在的理由(局中入座、没被发牌的人)。变异 `?? 0`(查不到就清零)全套绿着通过。已把 store 测试重写成一条三合一:carol(在座非参与者)不动、alice 打光到 **0**(顺带钉住 BUG-20 登记的第二条测试义务的数据前提)、alice 的 `stacks` 条目**故意填错 `seat_position`**——SeatStack 注释写明座位号只是信息性、客户端按昵称更新,这一填让「按昵称匹配」从测试标题上的一句话变成真能红的断言(复审指出旧夹具两个键一致,标题在超卖)。三个变异各自验证过变红。
- **旁观占座者测试**(核心层):`_finalize_hand` 若按「有人的座位」而不是 `hand.players` 遍历,现有各测里两个集合恰好重合(离桌者的座位在结算时也还占着)。新用例给桌上放一个 SITTING_OUT 的占座者,断言他不上 wire、座位筹码分毫未动;按集合重建的变异如期变红。
- 五处小项:`gen_wire_ts.py` import 失序(SeatStack 插错位)、BUGS.md 一处中日文引号不配对、BACKEND_GUIDE 把 `players[]` 说成 `player_acted` 也带(它只带行动者本人的 `points`)、本记录把「筹码后手」注释错归到 `reduce.py`(实在 `server.py` 的 `SeatView.points`)、复审用的 diff 快照过期(与两处后补的加固不同步)。

## 自 review

按 [review.md](../../review.md) 七维。本批动了**协议 + 结算钱路的事件产出 + 前端投影**,最高风险面是「stacks 与真实结算漂移」「前端借机复算规则」「必填字段砸了什么」。

- **① 分层 / 不变量**:core 仍纯同步——`stacks` 在既有 participants 循环里收集,无新分支、无 IO、无墙钟。**事件引用(不变量 7)核过**:`SeatStack` 是冻结 DTO 装 int,取值在 `s.points += p.points` 之后、`_evict` 之前,后续改座位不波及已产出对象。**钱路守恒不变**:没有新的钱移动,只是把已算出的数上 wire。隐私:座位筹码本就是公开广播面(`SeatView.points`/`PlayerBoughtIn.seat_points`),无新增暴露;`stacks` 不含底牌,`test_protocol` 的隐私断言照常绿。0091 的离场者 `Personal` 补发复用同一个 `HandEnded` 对象,`stacks` 一并带到——离场者本来就该看到自己那手怎么结的。
- **② 代码↔文档同步**:协议变了 ⇒ 四处前端可见文档同批(BACKEND_GUIDE 消息流 + 「别自己记账」新条目、wire-protocol-guide §3 行 + §5 时序、state.md 两行);codegen 重生成、`--check` 绿。`core.md` 只讲事件顺序不枚举字段,核过无需动。复审抓到 BACKEND_GUIDE 新写的那句自己就不精确(`players[]` 的携带者写错),已改——**给别人纠偏的那句话自己先要对**。
- **③ 文档↔文档一致**:BUGS.md 划掉 BUG-20 + 「已修复」表补行 + 修正登记里不精确的测试建议 + 写明第二条测试义务的落点与残余缺口;TODO.md 的 0105 指针补「0106 已修」。本批动过的文档相对链接扫过,0 死链。
- **④ 数据模型正确性**:`stacks` 设**必填**——可选的话「服务器忘了带」在类型上就成了合法状态;必填让五个旧测试夹具和 wire 样本在编译期逐个被点名,这正是要它必填的原因。`SeatStack` 带 `seat_position` 但注释写明**信息性**、客户端按昵称更新(0105 的键稳定性教训),且这条契约现在有测试钉着(故意错位的夹具)。
- **⑤ 规范合规**:新字段逐个含义注释;注释讲为什么(为什么挂 HandEnded、为什么含离桌者、为什么按昵称、为什么「查不到不动」);无魔法数、无死代码;`gen_wire_ts.py` 的 import 失序按复审修正(reduce.py 那处我自己排对了,同一双手两处两个样)。
- **⑥ 测试充分**:**7 处反向变异逐条按名确认**(服务端发空 ×3 测试名、store 忽略、store 换键、投影退回、重复条目、按占座重建)。**缺口如实记**:(a) e2e 的结算断言在 **chop 那一手会漏杀变异**——实测第一次变异跑就撞上一次平分,`100 == 98 + 2` 碰巧成立;确定性的杀在 vitest,e2e 管「管道真的通」,两层各司其职,不假装谁单独够。(b) **「打光筹码 → 买入输入框出现」的 JSX 渲染没有自动化钉**:store 测试钉到了门读的数(`mySeat().points === 0`),门本身的渲染要真输光一手 all-in,浏览器里造不出确定性的局;代码走读确认门会开,残余缺口记在 BUGS.md 划掉的条目里。(c) 三人边池的 `stacks` 没有专项断言(由 `smoke:raise` 的守恒 + 核心层 sidepot 测试间接兜住)。
- **⑦ 流程账本**:变更记录先行;「打算↔实际」无结构性偏离,两处计划外都在测试面并写明来由;复审确认 8 / 驳回 2、确认项全部当场修,清单在上节。提交信息英文、引用 0106。

### 收工时踩的两个坑,记下来

1. **变异实验期间撞上一次无法解释的红**:`test_showdown_single_pot_high_hand_wins` 在 reduce.py 应当干净的时刻红了一次,随后 7+ 次复跑全绿。**原因是复审 workflow 的 refute agent 就在同一个工作副本里做它自己的变异实验**(它的报告明写「applied the exact proposed mutation and ran the suite … original code restored」)——我的 pytest 和它的变异窗口撞在了一起。**教训:复审 agent 与收工验证不能并行**;本批的最终全套验证是在 workflow 结束之后单独跑的。
2. **变异后的恢复用了 `git checkout --`,把本批对 `room.ts` 的改动一并冲掉**(scratchpad 里那份 .bak 还是 0105 时代的旧文件)。靠 `grep -c stacks == 0` 当场发现,重新落了一遍并复验。**教训:变异的恢复要用「反向替换」或本批专属的备份**,`git checkout` 在未提交的批次中间是自毁按钮。

### 有意不做,留档

- **不给 `UserStatusChanged` 加筹码**:六七个产出点、手尾还天然漏掉离桌者,见「打算怎么改」。
- **三人边池的 e2e**:`smoke:raise` 已在协议层验边池结算 + 守恒,浏览器层的分层显示本来就没有(0080·B 记档的「界面只显示总底池」),等那个功能时一起。
- **BUG-21(dev 账号积分只出不进)**:测试基建,另一批。
