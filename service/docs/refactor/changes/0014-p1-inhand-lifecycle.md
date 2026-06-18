# 0014 · P1(三之三):局中生命周期 —— `_timeout` + 离桌 / 坐出 / 断线 / 清理(rules.md ④)

日期:2026-06-18 · 范围:`service/app/core/reduce.py`(+`_timeout`/`_leave_room`/`_disconnect`/`_cleanup`/`_set_user_status` + `_evict`/`_begin_leave` + `_finalize_hand` 驱逐整合 + `_acted_events` 抽取 + `_advance` len==1 短路)、`service/app/core/rules/sidepot.py`(修弃牌唯一最高者未叫注 forfeit,push 前 review 抓出的 money bug)、`service/app/core/domain.py`(+`Room.sitting_out_next`)、`service/app/core/records.py`(+`PointsWrite`)、`service/app/core/messages.py`(+`UserLeft`/`UserStatusChanged`)、`service/tests/core/{test_timeout,test_leave_sitout}.py`(新)+ `{test_sidepot,test_player_action}.py`(补)、文档同步(core/rules/models/lobby/TODO)。

## 背景 / 打算改什么

接 [0011](0011-p1-player-action-showdown.md)(`_player_action` + 街推进 + 摊牌 + 结算)与 [0013](0013-review-discipline.md)「下一步:回到 P1 局中离桌 + `_timeout`(rules.md ④)」。本篇落地**一手牌进行中 / 围绕一手牌的玩家会话生命周期**——对应 [rules.md](../../rules.md) ④(局中离桌 / 坐出 / 断线)+ [timer.md](../../timer.md) 的「行动倒计时 / 占座清理」两条到期流 + [core.md](../../core.md) §Command 的 `Timeout`/`LeaveRoom`/`Disconnect`/`Cleanup`/`SetUserStatus`。

这是「钱(积分)+ 隐私 + 状态机」三类风险叠加处:离桌要在**手未结束前不抽池中筹码**(守恒)、驱逐要**先退分 Persist 再 `del`**(顺序,见 [user.md](../../user.md))、断线要**保座等重连**(timer.md)。

**本篇范围(一个 TODO 簇,围绕「一手牌」的会话流转)**:

- `_timeout(work, Timeout)`:staleness(`hand.epoch != cmd.epoch` / 无手 / 行动者非 `cmd.nick`)→ 忽略(系统命令 `origin=None`,过期不报错);否则执行默认动作——**能 check 则 check,否则 fold**(timer.md),复用 `betting.apply_action` + 推进。
- `_disconnect(work, Disconnect)`:在房则 `UserStatus → OFFLINE`、**保座**(timer.md「断开 ≠ 离场」);在大厅 no-op。产 `Broadcast(UserStatusChanged)`。在局者仍是 `Player`,轮到他时由行动倒计时 `_timeout` 自动 fold(牌局不卡)。
- `_leave_room(work, LeaveRoom)`:
  - **在当前手内**(发起人是本手 `Player`):标 `room.leaving`,若仍 `ACTIVE` **立即 auto-fold**(即便能 check 也按弃,rules.md ④「他要走」);手尾由 `_finalize_hand` 结算后驱逐(forfeit 已投池中筹码、退还剩余栈)。
  - **不在当前手**(观战 / 坐出 / 两手之间):**立即驱逐**(`_evict`)。
- `_cleanup(work, Cleanup)`:staleness(仅 `OFFLINE` 才退筹释座,timer.md)→ 否则忽略;过则同 `_leave_room` 走 `_begin_leave`(在手内标 leaving、否则立即 `_evict`)。
- `_set_user_status(work, SetUserStatus)`:**仅本簇相关的就座内状态切换**——
  - `PLAYING → SITTING_OUT`(局中坐出):**延到手尾**(rules.md ④),标 `room.sitting_out_next`、本手 `PLAYING` 不变;`_finalize_hand` 据它把该人转 `SITTING_OUT`(而非 `SITTING_IN`),下手不发。
  - 就座内 ready/sit-out 切换(目标 ∈ `{READY_TO_PLAY, SITTING_IN, SITTING_OUT}`):查 `USER_STATUS_SELF_TRANSITIONS`,合法则改 + `Broadcast(UserStatusChanged)`。
  - 起身离座(`→ WATCHING`)/ 入座 / 买入:**不在本簇**,归后续座位/大厅簇(`Err(INTERNAL, "暂未实现")`,沿用 reduce `case _` 既有约定)。

**复用(不重造)**:`betting.apply_action`/`street_closed`(②)、`sidepot.settle`(③)、`_advance`/`_close_street`/`_settle_and_end`/`_finalize_hand`(0011)。本篇是 reduce 编排 + 抽取 `_acted_events`(`_player_action`/`_timeout`/离桌行动者 auto-fold 共用「快照行动结果 → 推进 → PlayerActed + 推进事件」)。

**本篇不做(留后续)**:`Connect` + `StateSnapshot`(重连,需 wire 未落地的快照报文)、`JoinRoom`/`SitDown`/`BuyIn`/`SetSmallBlind`/`SetBuyIn`(大厅/座位/买入簇)、`RoomChat`、免盲投票(①.12-15)、等大盲再入局时机(①.7-10)。

## 设计决策(开工前定的)

1. **新增域字段 `Room.sitting_out_next: set[str]`**:承载「局中请求坐出、延到手尾生效」的意图。不复用 `leaving`(语义不同:leaving=离房驱逐,sitting_out_next=留房但下手不打)。新增域字段属「代码用了非文档结构」⇒ 同篇同步 [core.md](../../core.md) Room 字段表 + [rules.md](../../rules.md) ④ 机制说明(coding_principle「双向同步」)。
2. **离桌驱逐统一走 `_evict(work, room, nick)`**(`room_name` 取 `work.room_name`):退座位筹码回全局积分(`UserState.points += Seat.points`)→ 产 `Persist(PointsWrite(uid, points))` → 释座(`seats[i]=None`)→ 移出 `users_in_room` → `del work.users[nick]` → `Broadcast(room, UserLeft)` + `Personal(nick, UserLeft)`。顺序守 user.md「退分 Persist 先于 `del`」、connection.md「离开者回执走 `Personal`(已不在成员名单)」。**未就座者(WATCHING)无座位筹码 → 不产 PointsWrite**。
3. **静态房不销毁**:v1 房间静态预置(lobby.md),`_evict` 后即便房空也保留 `work.room`(不置 None)。动态房回收留待动态建房簇(lobby.md「待定」)。
4. **手尾驱逐时机**:`_finalize_hand` 先(各 `Player.points` 还座 + 建手牌记录 participants,**含离桌者**——他们参与了本手)→ 设非离桌者 UserStatus(`sitting_out_next` → `SITTING_OUT`,否则 `PLAYING → SITTING_IN`)→ 再 `_evict` 每个 `leaving`(此时座位已含还回的剩余栈)→ 清 `leaving`/`sitting_out_next`。事件顺序:`HandEnded → Persist(record) → [每离桌者 Persist(PointsWrite)+Broadcast/Personal(UserLeft)] → ClearAction`。
5. **非行动者局中离桌 auto-fold**:若离桌者不是当前行动者,fold 后**不推进 turn**(当前行动者继续);仅当因此只剩 1 名未弃者才走 `_close_street` 结束本手。产 `Broadcast(PlayerActed FOLD)`(`acting_position` 不变)。
6. **default-action vs auto-fold 区分**:`_timeout` 默认动作「能 check 则 check」(对掉线者更友好,timer.md);`_leave_room`/`_cleanup` 的 auto-fold **一律 fold**(rules.md ④「即便能 check 也按弃」)——两者语义不同,不混用。
7. **载荷延续临时 dataclass**(messages.py / records.py):`PointsWrite`(Persist,挂 `PersistPayload`,user.md/db.md 已约定字段 `uid`/`points`)、`UserLeft`/`UserStatusChanged`(出站,挂 `ServerMessage`)。P4/P6 对齐 ORM/wire。隐私:三者结构上均无底牌/牌堆。

## 测试(orchestration + rules.md ④ 编号)

- `test_timeout.py`:默认 check(无注可过)/默认 fold(面对注)/staleness(`epoch` 不符 / 无手 / 行动者不符 → 忽略且 world 不动)/超时 fold 致单人结束。
- `test_leave_sitout.py`(rules.md ④ 编号):④.1 局中离桌即时 fold + 手尾驱逐(退栈/释座/移除/UserLeft+PointsWrite)、④.2 离桌致单人剩余手立即结束、④.3 局中坐出延到手尾转 `SITTING_OUT`、④.4 断线(OFFLINE+保座+超时 fold)vs 主动离桌(即时 fold+不留座)对比;另:两手之间离桌即时驱逐、非行动者离桌 fold 不推进 turn、离桌 ALLIN 者赢池带走、Cleanup 仅 OFFLINE 才驱逐(staleness)、就座内 ready/sit-out 切换。守恒 + 隐私断言默认开。

## 实际改了什么

新增 / 改:

- `app/core/reduce.py`:+`reduce` 五个 `match` 臂;+`_timeout`(staleness → 默认 check/fold,复用 `betting.apply_action`)、`_disconnect`(标 OFFLINE 保座 + `UserStatusChanged`)、`_leave_room`/`_cleanup`(→ `_begin_leave`)、`_set_user_status`(局中坐出延手尾 + 就座内 ready/sit-out 切换 + 起身→WATCHING/入座占位 INTERNAL);+`_begin_leave`(在手内标 leaving + ACTIVE 即时 auto-fold:行动者走 `_acted_events`、非行动者只产 `PlayerActed` 不推进 turn,仅 `len(live)==1` 才 `_close_street`;不在手内 `_evict`)、`_evict`(退筹 `PointsWrite` → 释座 → 移出 → `del users` → `UserLeft` Broadcast+Personal)、`_seat_of`/`_player_in_hand`;抽取 `_acted_events`/`_acted_broadcast`(`_player_action`/`_timeout`/离桌行动者共用);`_advance` 加 `len(live)==1` 短路(承下「自 review」②);`_finalize_hand` 整合坐出转 `SITTING_OUT` + 手尾 `_evict`(sorted)。
- `app/core/rules/sidepot.py`:**修 money bug(自 review ①)**——`settle` 第 1 步未叫注的唯一最高投入者**若已弃牌**(0014 auto-fold 可折掉高注者),未叫注不再退回本人,改作死钱归并到最高 live 子池;空 `eligible` 子池区分「弃牌唯一最高未叫注(归 live)」与「弃牌者互相匹配边池(退回 contributor)」。
- `app/core/domain.py`:+`Room.sitting_out_next`。`app/core/records.py`:+`PointsWrite`。`app/core/messages.py`:+`UserLeft`/`UserStatusChanged`(+ import `UserStatus`)。
- 测试:`tests/core/test_timeout.py`(新,6)、`tests/core/test_leave_sitout.py`(新,21)、`tests/core/test_sidepot.py`(+2 弃牌唯一最高者 forfeit)、`tests/core/test_player_action.py`(+1 heads-up SB 开弃回归)。
- 文档同步:`core.md`(Room 字段 + finalize 收尾/驱逐)、`rules.md` ④(机制)+ ③(未叫注弃牌者 forfeit / 空 eligible 两类)、`models.md`(PointsWrite/UserLeft/UserStatusChanged)、`lobby.md`、`TODO.md`。

**偏离计划**:开工范围未含 `sidepot.py`——它是 push 前 review 抓出的 money bug(0014 的 auto-fold 折掉唯一最高投入者,触发了 0007 sidepot 未设防的退还路径),属本篇正确性必修,故纳入本篇(见自 review ①)。

## 自 review(push 前,money path + 隐私 + 分层重点核对)

方法:7 维 + core 红线,**多代理对抗复审**(27 agents:7 维度 reviewer + 每候选 2 个「默认反驳」核实者),候选 10、双签确认 4、单签争议 1、驳回 5。确认项已全部当场修 + 同步文档。

1. **④ money path(确认并修)**:`sidepot.settle` 第 1 步把未叫注退给「唯一最高投入者」时**不查在局**。正常下注无碍(`betting` 禁止高注者弃牌),但本篇 auto-fold「即便能 check 也按弃」可折掉唯一最高投入者 → 旧逻辑把 forfeit 的注退回**离桌者**、在局存活者被少分(**离桌反获利**,守恒但误分配,违 rules.md ③④)。复审指出朴素 `top[0] in live` 不够(降额后落到空 eligible 退化档仍退回原投入者)。**已修**:弃牌唯一最高者的未叫注作死钱归最高 live 子池;空 eligible 区分「未叫注(归 live)/匹配边池(退 contributor)」。加 `test_sidepot` 2 例 + `test_leave_sitout` 端到端回归 `test_leave_by_lone_high_bettor_forfeits_uncalled_bet`。
2. **⑥/回归(确认并补)**:`_advance` 的 `len(live)==1` 短路并非纯 no-op——它顺带修了一个潜伏 bug:heads-up SB preflop **自愿开弃**时存活 BB `has_acted=False`、`street_closed` 为假,旧逻辑会卡着叫唯一存活者行动。已补 `test_headsup_sb_open_fold_ends_hand` 钉生产路径 + 补注释说明两条触发路径。
3. **⑥ 测试缺口(确认并补)**:多人同手离桌(`sorted(leaving)` 确定序驱逐)、局中 `SetUserStatus` 拒非 SITTING_OUT、断线幂等 + 不推进 turn、非行动者离桌致 `len(live)==1` —— 各补测试。
4. **②③ 文档同步**:change record 的 `_evict` 签名误写 4 参(实为 3)已修;sidepot 行为变更同步 rules.md ③;新字段/载荷同步 core/models/lobby。
5. **驳回(对抗核实未通过)**:分层/纯度/回滚/commit 传播均成立(0014 无新 IO/await、core 无 shell/db import);「conservation 未测高注者路径」「非行动者 len==1 未测」等被后续补测覆盖。**争议项**(断线幂等/turn 稳定断言)虽属防御性,仍补了断言与幂等测试(成本低、钉意图)。

**core 红线复核**:纯同步(grep 无 shell/db/asyncio import);失败 `return [], Err`、helper 不 raise;筹码守恒(每个驱逐/结算测试断言座位 + 退回全局总额);隐私(`UserLeft`/`UserStatusChanged`/`PointsWrite` 结构无底牌,测试断言);staleness(`epoch` / `OFFLINE` 兜)。

## 测试

`.venv/bin/pytest tests/ -q` → **130 passed**(0011 的 100 + 0014 的 30:timeout 6 + leave_sitout 21 + sidepot 2 + player_action 1)。覆盖:超时默认 check/fold + 三类 staleness、局中离桌即时 fold + 手尾驱逐(退栈/释座/移除/UserLeft+PointsWrite)、非行动者离桌不推进 turn / 致单人结束、离桌唯一最高者未叫注 forfeit 归 live(money 回归)、多人同手离桌确定序驱逐、ALLIN 离桌带奖金、坐出延手尾 + 拒非坐出、断线 OFFLINE 保座 + 幂等 + 不推进 turn、Cleanup 仅 OFFLINE/局中延迟、就座内状态切换 + 起身占位、heads-up SB 开弃回归;守恒 + 隐私默认开。

## 待办 / 下一步

- `Connect` + `StateSnapshot`(重连)、`JoinRoom`/`SitDown`/`BuyIn`/`Set*`(大厅/座位/买入簇)、`RoomChat`、免盲投票(①.12-15)、等大盲再入局时机(①.7-10)。
- `_set_user_status` 起身离座(`→ WATCHING`)+ 座位筹码处置:随座位/买入簇落地(本篇 `Err(INTERNAL)` 占位)。
- 动态房回收(最后一人离开销毁):随动态建房簇(本篇静态房保留)。
