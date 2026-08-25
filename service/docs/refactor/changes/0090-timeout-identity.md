# 0090 · 给 Timeout 一个够用的身份(BUG-3 + 0072·N4)

日期:2026-08-25 · 性质:**缺陷修复(core 命令/事件 + shell Timer)**· 触发:[BUGS.md](../BUGS.md) BUG-3,连带 [TODO.md](../TODO.md) 里并入它的 0072·N4。

## 缺陷是什么

[architecture.md](../../architecture.md)「过期防护」的承诺是:Timer 永远可能投出已经过期的命令,正确性靠 reduce 进门做 staleness 校验,判据是 `hand.epoch`。

问题在于 **`epoch` 不足以标识「哪一手」**:

- **跨手撞号(BUG-3)**:`Hand.epoch` 每手从 0 开始([domain.py](../../../app/core/domain.py) `epoch: int = 0`),所以「上一手的 epoch=3」和「这一手的 epoch=3」长得一模一样。Timer 已经把 `Timeout` 投进 inbox 之后、GameLoop 还没处理之前,前一手可能已经打完、新一手已经推进到同一个 epoch——这条陈旧命令于是被当成本手的有效超时执行,**把不该弃牌的人弃了**。
- **跨房撞号(0072·N4)**:`Timeout` 不带 room,`checkout` 用 `world.users[cmd.nick].room` 解析目标房——也就是**这个人现在在哪**。人换了房,陈旧的 `Timeout` 就落进新房。补 `hand.seq` 也堵不住这一条:`seq` 是**房内**单调的,两个房间各自的第 1 手都是 `seq=1`。

两条都要「同一批修」,因为它们是同一个缺口的两面:**`Timeout` 携带的身份不足以回答「你是为哪一手排的队」**。

## 先读设计文档(本仓纪律)

- [timer.md](../../timer.md):Timer 只投命令、不改状态;**取消是隐式的**(同房覆盖 / 触发即删),所以「陈旧命令必然存在」是设计前提,正确性全压在 reduce 的进门校验上。
- [architecture.md](../../architecture.md)「定时器」:判据是 `hand.epoch`,并明确 **不引入基于 wall-clock 的 `hand_id`**。所以身份要用已有的单调量拼,不能新造时间戳。
- 硬规则 8 / [storage.md](../../storage.md):命令不带 room,目标房由 `world` 推定(`JoinRoom` 是唯一例外)。

## 打算怎么改

给 `Timeout` 配一个**三元身份**:`(room, hand_seq, epoch)`,进门三项全等才算新鲜。

- `room` **不用于路由**——`checkout` 照旧从 `world.users[nick].room` 解析目标房,硬规则 8 的不变量原样保留。它只作**校验**:解析出来的房与命令自报的房不一致,就说明这条命令是别的房排的队,直接忽略。这是对规则 8 的一处**澄清**而非破例,要写进 [storage.md](../../storage.md) 与 [timer.md](../../timer.md)。
- `hand_seq` 取 `hand.seq`(= 开局时的 `room.hand_seq`,房内单调,已用于手牌记录 dedupe)。它把跨手撞号堵死。
- 两者都要经 `TurnChanged` 交给 Timer(Timer 不读 world),所以 `TurnChanged` 也补 `hand_seq`(`room` 它已经有了)。

`Cleanup` **不改**:它同样按 nick 解析房,但 `_cleanup` 的判据是「仍 `OFFLINE`」——人换到别的房必然是在线的,陈旧 `Cleanup` 天然被挡。这一条要在变更记录里论证,免得下一个人「顺手对齐」。

## 要动的文件(预期)

- `app/core/commands.py`(`Timeout` 加两字段)、`app/core/events.py`(`TurnChanged` 加 `hand_seq`)、`app/core/reduce.py`(3 处 `TurnChanged` 产出 + `_timeout` 校验)
- `app/shell/timer.py`(`_ActionDeadline` 带 `hand_seq`、构造 `Timeout` 带齐)、`app/shell/dispatch.py`(转交)
- 测试:`tests/core/test_timeout.py`(跨手 / 跨房两条交错)、`tests/shell/test_timer.py`
- 文档:[timer.md](../../timer.md)、[core.md](../../core.md)、[architecture.md](../../architecture.md)、[storage.md](../../storage.md)、[BUGS.md](../BUGS.md)(划掉 BUG-3)、[TODO.md](../TODO.md)(N4 兑现)

协议面不变(`Timeout` 是系统命令,没有对应报文),所以 `BACKEND_GUIDE.md` 与 codegen 不动。

## 实际改了什么

按计划落地,**没有偏离**。`Timeout` 从只带 `epoch` 改成带三元身份 `(room, hand_seq, epoch)`,`_timeout` 进门三项全比。

### 后端

- **`core/commands.py`**:`Timeout` 加 `room: str` 与 `hand_seq: int`,两个字段的注释都写清「room 不是路由字段」。
- **`core/events.py`**:`TurnChanged` 加 `hand_seq`(`room` 它本来就有)——Timer 不读 `world`,身份只能由事件带过去。
- **`core/reduce.py`**:3 处 `TurnChanged` 产出点补 `hand_seq=hand.seq`;`_timeout` 的 staleness 从一项变三项,**顺序是 room → seq → epoch**(从粗到细,便于读)。
- **`shell/timer.py`**:`_ActionDeadline` 记 `hand_seq`;`on_turn_changed` 多收一个参数;`tick` 构造 `Timeout` 时把排队那一刻的 `room` 一并带上(它就是 `_action` 的键,取用是免费的)。
- **`shell/dispatch.py`**:`TurnChanged` 的解构多一项,转交 Timer。

### 没动的地方,以及为什么

- **`Cleanup` 不加身份。** 它同样按 nick 解析房,但 `_cleanup` 的判据是「仍 `OFFLINE`」:人要是换到了别的房,必然是在线的,陈旧 `Cleanup` 天然被挡住。写进 [timer.md](../../timer.md) 一句「别顺手对齐」,免得后人看到 `Timeout` 有三元身份就照着给它也加。
- **硬规则 8 不变。** `checkout` 解析目标房的表([storage.md](../../storage.md))一个字没改,`Timeout` 仍按 `world.users[nick].room` 路由;命令自报的 `room` 只参与校验。这是对规则 8 的**澄清**,不是第二个例外——已在 storage.md 与 timer.md 各写一句。
- **协议面零改动**:`Timeout` 是系统命令,没有对应报文,codegen 与 [BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 不动。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **752 passed**(750 → 752,新增 2 条 core 回归 + 1 条 shell 断言扩展) |
| 前端 vitest | 90 passed(未改前端) |
| 浏览器 `npm run test:e2e` | 16 passed |
| 三条冒烟 | 全部通过 |
| 后端改完重启 uvicorn 再跑前端各层 | 是 |

**反向变异验证 3 处**:

| 变异 | 变红的 |
|---|---|
| 去掉 `hand.seq != cmd.hand_seq` 校验(退回 BUG-3)| `test_timeout_from_previous_hand_ignored` |
| 去掉 `work.room_name != cmd.room` 校验(退回 N4)| `test_timeout_scheduled_in_another_room_ignored` |
| Timer 投 `hand_seq=0`(身份没真的带出来)| `test_action_timeout_fires_with_full_identity` + `test_turn_changed_and_clear_action_drive_timer` |

两条新回归测都**刻意只让一项不同**:`test_timeout_from_previous_hand_ignored` 的 room/nick/epoch 全部对得上、只有 `seq` 差一;跨房那条 seq/epoch/nick 全对、只有 room 不同。不这样构造的话,测试可能是被别的判据挡下的,修法删掉也照样绿。

## 自 review

按 [review.md](../../review.md) 七维。本批改的是**系统命令的身份**,最高风险面是「判据是否真的完备」与「会不会把该执行的超时也误挡掉」。

- **① 分层 / 不变量**:core 仍纯同步;新判据全是内存里的单调量,**没有引入墙钟**(不变量 1 / architecture.md 明写不要 wall-clock `hand_id`)。硬规则 8 逐字复核过:`_target_room` 一行未改,`Timeout.room` 不参与路由。Timer 仍只投命令、不碰 `world`。
- **② 代码↔文档同步**:[timer.md](../../timer.md)(伪码里的 `_ActionDeadline`/`on_turn_changed`/`tick`/staleness 段全部改实,并加了一张「三项各挡什么」的表)、[core.md](../../core.md)「手牌标识与 staleness」(原文只说 epoch 判新鲜、seq 判哪一手,现在写清两者都不够)、[architecture.md](../../architecture.md)「定时器」、[storage.md](../../storage.md)(checkout 表下补一句澄清)。
- **③ 文档↔文档一致**:[BUGS.md](../BUGS.md) 划掉 BUG-3 并**更正登记的修法**——它写的「双键」不够,实际是三键;同时点明 BUG-2 虽同源但仍按用户定案暂缓,别被这批顺手带走。[TODO.md](../TODO.md) 的 N4 注记改成兑现记录。
- **④ 数据模型正确性**:三项都是**必填无默认**,漏填是构造期错误(实测:改完先跑测试,11 个用例立刻红,正是它们各自漏填)。`room` 用 `str` 而非 `str | None`:Timer 的 `_action` 以 room 为键,排队时必有房名,不存在「无房的行动倒计时」这种可表达的非法态。
- **⑤ 规范合规**:字段逐个带中文含义注释;无魔法数;注释讲「为什么」——尤其两处反直觉点:为什么 `room` 不是路由字段、为什么 `Cleanup` 不需要同款身份。
- **⑥ 测试充分**:3 处反向变异确认,两条新回归测按「只差一项」构造(见上)。**如实记缺口**:(a) 没有一条**端到端**用例真的制造出「陈旧 Timeout 在新一手里到达」的交错——那要么塞满 inbox、要么在 GameLoop 里插桩,当前 fake 做不出来;core 层是直接喂命令验判据,shell 层是验身份有没有被带出来,两头都测了、中间那段交错靠推理。(b) 跨房那条同理,是直接喂一条 `room="r2"` 的命令,没有真的让玩家换房。
- **⑦ 流程账本**:本篇即账本,开工前写「打算」(含「为什么补 seq 还不够」的论证)、收工回填,无偏离。提交信息引用 0090。

### 顺带发现,未在本批处理

- **`Timeout` 与 `Cleanup` 的 staleness 判据不对称**,现在是有意为之并已写进文档。但两者的「过期忽略」都**不落日志**——真出问题时(比如判据写错把有效超时也挡了)现象是「桌子卡住、谁也不动」,而日志里一片安静。要不要给过期忽略加一条 DEBUG,值得单独议。
