# 0006 · P0 review 跟进:补注释 + 修文档漂移 + 补测试

日期:2026-06-17 · 范围:`service/app/core/cards.py`、`app/core/domain.py`、`docs/coding_principle.md`、`docs/core.md`、`docs/connection.md`、`docs/timer.md`、`tests/`

## 缘起

对已落地但未 push 的 0002–0005(P0 基线)做了一次多视角 code review(分层纯度 / 文档↔代码一致 / 数据模型正确性 / 代码与测试质量),逐条对抗式核实。**生产代码本身正确**(core 纯同步、`checkout`/`commit` 工作副本逻辑无误、命令/事件/错误集与文档一致、frozen+slots 继承字段序合法,24 测试全绿),无阻断项。只暴露 4 个 minor/nit,且其中两个与 0004/0005 自己「全部字段已补注释」「文档已同步」的声明相矛盾——按本项目「文档↔代码不一致是缺陷不是待办」(coding_principle §通用规范)的纪律,push 前一并修掉。

## 改了什么

### 1. 补漏的字段/成员注释(coding_principle L41)

- `domain.py`:`Hand.flop/turn/river` 三个公共牌字段是全 core+shell 唯一漏注释的字段(夹在已注释的 `deck`/`contributed`/`epoch` 之间),补「第 N 张公共牌;None = 未发」。这正是 0004 声称「全部字段已补」却漏掉的三个。
- `cards.py`:`CardSuit` 逐成员补中文花色名(`h/d/c/s` 是缩写,注释有信息量);`CardRank` 在枚举上方一行说明取值编码。

### 2. 自文档化值枚举的注释例外(coding_principle L41)

- `CardRank` 的 `"2".."9"` 取值即含义,逐成员注释只会复述代码、反与「注释讲为什么不复述」(L39)打架。把这个**例外**写进 coding_principle.md L41:取值即含义的自文档化值枚举在枚举上方一行说明编码即可,缩写型取值(花色)仍逐成员标。避免规则与代码再次漂移、也避免后续 review 重复 flag。

### 3. 修文档↔代码漂移(coding_principle L42)

- **`core.md` L31**:UserStatus 合法转移表的链接仍指向旧 `../app/pokertable/enums.py`,但 0002 已把表迁到 `app/core/enums.py` 且**内容已分叉**(core 版补了 `(SITTING_IN,OFFLINE)`/`(OFFLINE,SITTING_IN)`)。改指向 `app/core/enums.py`,免得读者跟链接落到过时的权威表。
- **`TurnChanged` 的 `timeout_s`**:落地事件是 `TurnChanged(room, acting_nick, epoch)`(无 `timeout_s`,见 0003-D①「Timer 自读配置」的决定),但 `connection.md`/`timer.md` 的派发/构造伪码仍按 4 字段解构/构造,指向不存在的字段。本次把这两处伪码改成 3 字段,并注明倒计时长由 Timer 取 `gameconfig.ACTION_TIMEOUT`(`on_turn_changed(timeout_s=None)` 即走配置,留参仅作可选覆盖)。补上了 0003 承诺「在 timer.md 注明」却一直没做的同步。

### 4. 补/强化测试

- `tests/shell/test_world.py`:`test_failed_command_leaves_world_untouched` 原来只「改副本不 commit → 断言 world 不变」,与 `test_checkout_deepcopies_room_and_users` 实质重复、且没真正走「失败丢弃 vs 成功落定」的对照。改名 `test_commit_or_discard_is_the_only_rollback`,在同一份被改副本上先断言「不 commit → 不变」再 `commit` 断言「落定」,真正钉住「回滚 = 没 commit」。
- `tests/core/test_domain.py`:新增 `test_userself_transition_distinguishes_system_from_player`,覆盖此前零调用的 `userself_can_change_to`,并锁定「系统转移(READY_TO_PLAY→PLAYING)合法但不在玩家可自发子集」这条区分。

## 验证

- 24 个测试全绿;core 纯度不受影响(只动注释 + 文档 + 测试,无新导入)。

## 待办 / 下一步

- 进 P1 时:0003-D 的遗留项仍带走(`timeout_s` 归属在 P3 接 timer 时定死、ErrorCode 随用随定、wire/db payload 收紧、`StartHand.seat` 取舍)。
