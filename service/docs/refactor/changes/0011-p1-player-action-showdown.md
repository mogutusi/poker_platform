# 0011 · P1(三之二):`_player_action` + 街推进 + 摊牌 + 结算/边池

日期:2026-06-18 · 范围:`service/app/core/reduce.py`(+`_player_action` 及推进/摊牌/结算 helper)、`service/app/core/messages.py`(+动作/摊牌/结束出站载荷)、`service/app/core/records.py`(新:`HandRecordWrite` 事件写载荷)、`service/app/core/deck.py`(+`BOARD_CARDS`)、`service/tests/builders.py`(+`card`/`deck_for`/`hand_world`/`player(hole=)`)、`service/tests/core/test_player_action.py`(新)、`service/tests/core/test_start_hand.py`(born-all-in 用例改判)、文档同步。

## 背景 / 打算改什么

接 [0010](0010-p1-reduce-start-hand.md):reduce 已落 `_start_hand`(开局)。本篇落**一手牌的后半**——`_player_action` + 下注轮推进 + 街切换 + 摊牌 + 边池结算 + 手牌记录(对应 [core.md](../../core.md) §2-4、[rules.md](../../rules.md) ②③)。这是「最易出钱(积分)错」的结算路径。

**关键复用**:规则纯函数已在 0007/0008 落地并穷举单测——`betting.apply_action`/`street_closed`/`settle_street`/`next_active_position`(②)、`sidepot.settle`(③)、`blinds`(①)。本篇是 reduce 层的**编排**:把这些接成「动作→推进→摊牌→结算→落库→收尾」,**不重造规则、不在 reduce 重测规则内部**(testing.md:reduce 测编排,不重测 core 已覆盖)。

**本篇范围**:

- `_player_action`:校验(有 hand、`acting_position` 指向 origin)→ `betting.apply_action` → 推进。产 `Broadcast(PlayerActed)` + 推进事件。
- 推进 `_advance`:`street_closed` → 结算分支;否则换下一个 ACTIVE(`epoch+=1` + `TurnChanged`)。
- 街结算 `_close_street`:`settle_street` 并入 contributed,然后按 [rules.md](../../rules.md) ③ 分支——① 仅 1 人未弃 → 无摊牌结束;② ≤1 人可行动(其余 all-in)或 RIVER 关闭 → 摊牌;③ 否则发下一街公共牌 + postflop 首行动位(`_postflop_first`)。
- 摊牌/结束 `_settle_and_end`:摊牌补齐公共牌 + `evaluate` 牌力 + `Broadcast(HandShowDown)`(底牌唯一合法公开点);跑 `sidepot.settle`;分配赢得/退还进 `Player.points`。
- 收尾 `_finalize_hand`:`Player.points` 还回 `Seat`、清 `in_game_points`、`PLAYING→SITTING_IN`;产 `Broadcast(HandEnded)` + `Persist(HandRecordWrite)` + `ClearAction`;`room.hand=None`、`PENDING_START`。
- **补 0010 §6 待办**:`_start_hand` 末尾若 `acting_position is None`(born-all-in,全员投盲即 all-in)→ 立即走 `_close_street` 跑公共牌摊牌,不卡死。`test_all_dealt_all_in_on_blinds_no_actor` 据此改判(从「停在 HAND_STARTED」改为「跑完结算、回 PENDING_START」)。

**本篇不做(留后续)**:

- [rules.md](../../rules.md) ④ 局中 `LeaveRoom`/`SITTING_OUT`/断线 auto-fold + 手尾驱逐(需 LeaveRoom/SetUserStatus/Timeout handler;`room.leaving` 本篇恒空)。
- `_timeout`(超时默认 check/fold)、`_connect`/lobby/连接簇、`StateSnapshot`。
- 等大盲再入局时机(①.7-10)、免盲投票 handler(①.12-15)——仍承 0010。

## 设计决策(开工前定的)

1. **出站/落库载荷延续 0010「临时 dataclass」**:`PlayerActed`/`HandShowDown`/`ShowdownReveal`/`HandEnded`/`NickAmount` 进 [messages.py](../../../app/core/messages.py);`HandRecordWrite`/`ParticipantWrite` 进新 [records.py](../../../app/core/records.py)(Persist 载荷,挂 `PersistPayload`,与「出站消息」分文件)。P4/P6 对齐 ORM/wire(见 [db.md](../../db.md)「Persist 接口」/[wire.md](../../wire.md))。隐私:只 `HoleCards`/`HandShowDown` 带底牌。
2. **牌堆长度一次校验到底**:`_start_hand` 把 `< 2N` 改为 `< 2N + BOARD_CARDS(5)`——一手最多需底牌 2N + 公共牌 5。这样后续发公共牌的 helper **永不缺牌**,守 helper「绝不 raise」(生产恒 52 张,无影响;过短注入仍 `Err(INTERNAL)`)。
3. **跑公共牌一次发齐、不发逐街 `HandStatusChanged`**:`≤1` 人可行动时 `_run_out_board` 把未发公共牌一次补齐,`HandShowDown` 携带完整 5 张 board;不为 runout 的中间街产 `HandStatusChanged`(符合 [rules.md](../../rules.md) ③「一次发齐」)。
4. **无摊牌结束也统一走 `sidepot.settle`**(单一未弃者为唯一 eligible,未叫注由算法退还),不特判金额(照 [rules.md](../../rules.md) ③「无摊牌结束」)。
5. **`HandRecordWrite.final_pot` = 各子池金额之和**(不含退还);`participants` 每人 `uid`(取 `work.users[nick].uid`)+ `initial_points`(`in_game_points` 快照)+ `final_points`(还回座位后)。`end_time` 留给 shell 派发时盖墙钟(core 不读时钟)。
6. **`PlayerActed` 反映推进后状态**:行动者动作 + 其结果栈(快照于 settle 前),`last_bet`/`pot`/`acting_position` 取推进后值(手结束为 `None`)。

## 测试(orchestration,不重测规则内部)

`test_player_action.py`:动作校验臂(NO_HAND/NOT_YOUR_TURN/ILLEGAL_ACTION 透传)、街内换人(TurnChanged/epoch)、preflop 大盲选择权经 reduce、加注重开继续、街关闭→进街(发公共牌/postflop 首行动/HandStatusChanged)、摊牌(HandShowDown+HandEnded+Persist+ClearAction、还座、守恒、隐私)、无摊牌结束、all-in 跑公共牌、边池分配经 reduce 正确还座。`test_start_hand.py`:born-all-in 改判。

## 实际改了什么

新增 / 改:

- `app/core/reduce.py`:+`_player_action`(校验 → `betting.apply_action` → `_advance`,产 `Broadcast(PlayerActed)` + 推进事件);+`_advance`(街关 → `_close_street`;否则换 ACTIVE + `epoch++` + `TurnChanged`);+`_close_street`(`settle_street` → 分支:1 人未弃=无摊牌 / ≤1 可行动或 RIVER=摊牌 / 否则发下一街 + `_postflop_first`);+`_settle_and_end`(摊牌补 board + `evaluate` + `HandShowDown`;`sidepot.settle`;分配进 `Player.points`);+`_finalize_hand`(还座 + 清锁筹 + `PLAYING→SITTING_IN` + `HandEnded` + `Persist` + `ClearAction` + `room.hand=None`/`PENDING_START`);+纯计算 `_deal_board`/`_run_out_board`/`_board`/`_postflop_first`/`_pot`/`_by_nick`。`match` +`PlayerAction` 分支;`_start_hand` 末尾 born-all-in 接 `_close_street`;牌堆校验 `< 2N` → `< 2N+BOARD_CARDS`。
- `app/core/messages.py`:+`PlayerActed`/`ShowdownReveal`/`HandShowDown`/`NickAmount`/`HandEnded`(临时出站载荷,隐私:仅 `HoleCards`/`HandShowDown` 带底牌)。
- `app/core/records.py`(新):`HandRecordWrite`/`ParticipantWrite`(Persist 事件写载荷,挂 `PersistPayload`)。
- `app/core/deck.py`:+`BOARD_CARDS=5`。
- `tests/builders.py`:+`card`/`deck_for`/`hand_world`、`player(hole=)`。
- `tests/core/test_player_action.py`(新,12 测试)+ `tests/core/test_start_hand.py`(`test_all_dealt_all_in_on_blinds_no_actor` → `test_born_all_in_runs_out_and_settles` 改判)。
- 文档同步:`models.md` 待定(+records.py)、`TODO.md`(reduce/tests 进度)。

**计划内、未偏离**:规则纯函数零改动(只编排),core 纯度复验通过(`grep` 无 shell/db/asyncio/fastapi import)。

## 自 review(push 前,money path 重点核对)

手动对抗核对了结算路径的几处易错点,均确认无误:

1. **未叫注退还绝不落到弃牌者**:`sidepot` 的 refund 给「唯一最高投入者」。能弃牌(`betting.FOLD`)的前提是 `bet_amount < last_bet`,即弃牌者**永远不是**最高投入者 → refund 永远给在局者。无摊牌结束统一走 `sidepot.settle`(单一 eligible),不特判金额。
2. **筹码守恒**:`Σ Player.points(剩余 + 赢得/退还) == Σ in_game_points`(`payout.total == Σ contributed`,`Σ contributed + Σ 剩余 == Σ 锁入`);每个结算测试断言座位总额守恒(单池 450 / 边池 350 / runout 120 / 无摊牌退还 220)。
3. **born-all-in 不卡死**:`_start_hand` 末尾 `acting_position is None` → `_close_street` 跑公共牌摊牌(完成 0010 §6);改判用例钉死「回 PENDING_START」。
4. **`_advance` 换人不返 None**:街未关 ⇒ 必有「另一个」ACTIVE(`street_closed` 谓词保证),`assert` 兜底。
5. **发公共牌不缺牌**:`_start_hand` 一次校验 `2N+5`,后续 `_deal_board`/`_run_out_board` 永不缺牌(守 helper 绝不 raise);生产恒 52 不受影响。
6. **隐私**:`PlayerActed`/`HandEnded`/`HandStatusChanged` 结构上无 `hole_cards`;底牌仅 `HoleCards`/`HandShowDown.reveals`。测试断言广播无底牌字段。

## 测试

`.venv/bin/pytest tests/ -q` → **100 passed**(0010 的 88 + 本篇 12)。覆盖:校验臂(NO_HAND/NOT_YOUR_TURN/ILLEGAL_ACTION 透传 + 失败丢副本)、街内换人(TurnChanged/epoch)、preflop 大盲选择权经 reduce、多街自然推进到摊牌、街关闭→发公共牌→postflop 首行动、摊牌单池/边池经 reduce 正确还座、无摊牌结束 + 未叫注退还、all-in 跑公共牌、手牌记录字段(dedupe_key/initial/final/final_pot)、守恒 + 隐私。

## 待办 / 下一步

- **rules.md ④ 局中离桌/坐出/断线**:`LeaveRoom`(即时 auto-fold + 手尾驱逐 `room.leaving`)、局中 `SITTING_OUT`(延到手尾)、断线超时 fold——需 LeaveRoom/SetUserStatus/Timeout handler;`_finalize_hand` 届时按 `room.leaving` 退筹释座(本篇恒空)。
- `_timeout`(超时默认 check/fold)、免盲投票(①.12-15)、等大盲再入局时机(①.7-10)、连接/lobby/`StateSnapshot` 簇。
- `reduce` `case _` 随各 handler 落地继续收缩。
- P4 落地时 `HandRecordWrite` 对齐 ORM + `Persist` 经写缓冲(`end_time` 由 shell 盖墙钟)。
