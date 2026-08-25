# core 游戏状态机(reduce)

## 定位

一句话:core 是纯同步的游戏规则层,只做「旧状态 + 命令 → 新状态 + 事件」的计算,不碰任何外部世界。

- 签名 `reduce(work, cmd) -> (list[Event], Err | None)`:输入只认 `Command`,输出只有 `Event` 和 `Err`。
- 不 import FastAPI / SQLAlchemy / WebSocket,不 `await`、不碰 DB、不读墙钟(不变量 1)。
- `reduce` 改的不是 `world` 本体,而是 GameLoop 递进来的**工作副本**——即当前命令涉及部分的一份可改的临时拷贝。成功就 commit 写回,失败或异常就整份丢弃(commit-or-discard,见 [storage.md](storage.md))。

原型 `gamelogic.py`/`services.py` 含 bug(如 `pots.values().sum()`、`hand.handstatus`、`do_action` 里循环变量覆盖入参),不作为事实来源。它们已在 0027 拆除,只存于 git history。

## 域模型(core 的权威状态)

一句话:core 的权威状态是 `world`,和发给客户端的 wire DTO 是两套东西(见 [wire.md](wire.md))。

字段清单以 [`app/core/domain.py`](../app/core/domain.py) 为准,每个字段都带中文注释。这里只列各实体职责:

| 实体 | 职责 | 要点 |
|---|---|---|
| `World` | 内存权威:全部房间 + 全局用户表 | `users` 键为 nick,只装在房用户,大厅用户不进这张表(见 [user.md](user.md)) |
| `Room` | 一个牌桌的全部状态 | 定长 `seats`、`button_position`、`hand_seq`;`entry_vote`/`waive_entry_for` 服务**免盲投票**——新玩家想不付盲直接入局,需已入局玩家全票同意(见 [rules.md](rules.md) ①);`leaving`/`sitting_out_next` 服务局中离桌、坐出(见 [rules.md](rules.md) ④);`chat_history` 是房聊环形历史,纯展示,规则不读,随房生灭(0071) |
| `EntryVote` | 免盲投票进行态 | `approvals` 集合;任一 `rejected` 即失败(见 [rules.md](rules.md) ①) |
| `Seat` | 在桌的钱与身份,跨手牌存活 | `points` 是桌上筹码,`in_game_points` 是锁入本手的快照;`new_here`/`wait_for_big_blind` 记入局方式 |
| `Hand` | 一手牌的全部状态 | `players` 按行动序排列,`[0]` 是 SB、`[1]` 是 BB;`contributed` 是本手累计投入(旧名 pots);`last_bet`/`last_raise_size` 供下注规则使用;`epoch` 是 staleness(新鲜度)标记,`seq` 是手牌标识,`start_time` 由 shell 盖、core 不读 |
| `Player` | 在这一手里的状态,手尾即弃 | `status`、`points`、`has_acted`(见 [rules.md](rules.md) ②);`bet_amount` 是本街投入;`hole_cards` 是隐私字段 |

另外两条:

- `hole_cards`/`deck` 是隐私(不变量 3,见 [log.md](log.md))。
- `Hand.start_time` 由 shell 在 `StartHand` 里盖好带入,core 只存不读,不据它分支。

## 状态机(四套,各管一层)

一句话:房间、用户、手牌、玩家各有一套状态,互不代替。

| 状态机 | 取值 | 谁推进 |
|---|---|---|
| RoomStatus | `PENDING_START` → `HAND_STARTED` → (回到)`PENDING_START` | StartHand / 手牌结束 |
| UserStatus | `WATCHING`/`SITTING_IN`/`READY_TO_PLAY`/`SITTING_OUT`/`PLAYING`/`OFFLINE` | 玩家操作 + 连接生命周期 |
| HandStatus | `PRE_FLOP` → `FLOP` → `TURN` → `RIVER` → `SHOWDOWN` → `ENDING` | 下注轮关闭(`next_status`) |
| PlayerStatus | `ACTIVE` / `FOLDED` / `ALLIN` | 玩家动作 |

UserStatus 的合法转移表见 [enums.py](../app/core/enums.py)。

`UserStatus` 与 `PlayerStatus` 正交:前者是这个人在房间里的身份,后者是这一手里该座位的牌局状态。

开局把 `READY_TO_PLAY` 的人变 `PLAYING`,结束变回 `SITTING_IN`。所有 UserStatus 转移必须查 `USER_STATUS_TRANSITIONS` 合法表,非法转移 `return [], Err(...)`。

## Command 全集(开放集合)

一句话:游戏命令不带房间号,目标房由发命令的人推出来——因为**模型 2**,一个用户同时只在一个房间。

- 目标房 = `world.users[nick].room`,reduce 和 `checkout` 都据此解析;只有 `JoinRoom` 例外,它的目标房写在命令里。
- `origin` 是 nick,因为连接绑 nick(见 [connection.md](connection.md))。

| Command | origin | 来源 | 语义 |
|---|---|---|---|
| `JoinRoom(room, uid, loaded, create?)` | nick | wire | 大厅→房间 |
| `LeaveRoom()` | nick | wire | 退分离桌,回大厅;从 `world.users` 移除 |
| `SitDown(seat, wait_for_big_blind)` | nick | wire | 观战→入座,并声明入局方式 |
| `BuyIn(seat, amount)` | nick | wire | 全局积分→座位筹码 |
| `SetUserStatus(status, seat)` | nick | wire | ready / sit-out / 起身等 UserStatus 转移 |
| `SetSmallBlind(amount)` / `SetBuyIn(amount)` | nick | wire | 配置房间参数 |
| `StartHand(seat, started_at, deck?)` | nick | wire | 开新一手 |
| `PlayerAction(action, bet_amount?)` | nick | wire | fold / check / bet |
| `RoomChat(text)` | nick | wire | 房间聊天 |
| `OpenFreeEntryVote()` | nick | wire | 有 `new_here` 时开一次免盲投票 |
| `VoteFreeEntry(approve)` | nick | wire | 对免盲投票表态 |
| `Connect(nick)` | None | shell | 握手后接入大厅 |
| `Disconnect(nick)` | None | shell | ws 断开 |
| `Timeout(nick, epoch)` | None | Timer | 行动超时 |
| `Cleanup(nick)` | None | Timer | 占座到期清理 |

各命令细则:

**`JoinRoom`**

- `uid`/`loaded` 是 DB 读出的账号主键与积分;已在别房 → `ALREADY_IN_ROOM`,成功则把用户装入 `world.users`,状态 WATCHING。
- `ROOM_FULL` v1 不强制(见 [lobby.md](lobby.md));房不存在时的建房流程见「房间生命周期」(0022/0049)。

**`SitDown`**

- `wait_for_big_blind` 声明入局方式:等大盲免费,或默认**付盲即玩**——立刻补一份盲注就能马上参与这手(见 [rules.md](rules.md) ①)。

**`SetSmallBlind` / `SetBuyIn`**

- 任何在房成员都能改,没有房主(0044);大盲不单独设,由 `2×小盲` 派生。
- 仅两手之间可改,局中拒绝并回 `HAND_IN_PROGRESS`;这是正确性校验,不是授权检查。
- 上下限由 shell 按 `gameconfig.MIN/MAX_*` 防护,越界回 `INVALID_SMALL_BLIND`/`INVALID_BUY_IN`。
- 产出 `Broadcast(RoomConfigChanged)`;不落库,房状态不持久(见 [storage.md](storage.md))(0043)。

**`StartHand`**

- `started_at` 是墙钟,由 shell 盖好带入;`deck` 可选,重放用。

**`PlayerAction`**

- 下注、跟注、加注合并为 BET 一个动作 + 金额。

**`RoomChat`**

- 追加进 `Room.chat_history`(0071),产出 `Broadcast(ChatMessage)`,除此之外不改游戏状态;私聊不走这里(见 [messaging.md](messaging.md))。

**`VoteFreeEntry`**

- 已入局玩家全票 `approve`,则新玩家免费入局。

**`Connect`**(握手后接入大厅,命令本身不带 room/积分)

按 world 里这个 nick 的现状分三类:

1. nick 在房且 `OFFLINE` → 重连恢复:改状态 + 广播 + 私发 `Personal(StateSnapshot)`。
2. nick 在房且在线 → **顶替再连**,即同一账号又开了一条连接、把旧连接顶掉:只私发快照对齐新连接,状态不变、不广播。
3. nick 不在 `world.users` → 纯大厅,core 无事。

细节见 [connection.md](connection.md)。

**`Disconnect`**

- 观战者即时离场,若他是最后一人则连带销房(0070);在座者标 `OFFLINE` 保座;人在大厅则 world 无变化。

**`Timeout`**

- 目标房由 `world.users[nick].room` 定。

通用规则:

- `origin` 决定错误回发给谁(见 [error.md](error.md));系统命令 `origin=None`,失败只落日志。
- 客户端命令与 wire `ClientMessage` 1:1;系统命令没有报文。
- `JoinRoom`/`LeaveRoom` 的房间生命周期细节见 [lobby.md](lobby.md)。

## 房间生命周期(创建 / 销毁)

一句话:房间是用出来的——谁都能建,空了就没。

**动态房是唯一模型**(0049):无静态预置房,谁都可创建、空则销毁,创建者无特权、没有房主(同 0044)。

**创建**

1. `JoinRoom` 到不存在的房时,`checkout(world, cmd)` 给出一份 `work.room is None` 的工作副本。
2. reduce 用 `cmd.create` 新建 `Room`:空座、`PENDING_START`。
3. 把用户加为 WATCHING。
4. `commit` 把新房插回 `world.rooms`(见 [storage.md](storage.md))。

关于 `cmd.create`:

- 类型是 `RoomCreate{small_blind,buy_in,seats,chat_history_size}`,由 shell 从 `gameconfig` 盖上,因为 core 不 import config;client 报文 `join_room{room}` 不含建房配置。
- 建后任何在房成员可用 `SetSmallBlind`/`SetBuyIn` 调参。
- `create=None` 且房不存在 → `NO_SUCH_ROOM`。这是防御分支,shell 应总带 `create`。

**销毁**

- 触发条件是最后一人离开;实现收在 `reduce()` 顶层一处,守住「已提交的房永不为空」:任一成功命令之后,若目标房 `users_in_room` 变空,就置 `work.room=None`,由 `commit` 销毁。
- 这一处覆盖全部清空路径:`LeaveRoom`、`Cleanup`、手尾 `_finalize_hand` 驱逐等。
- 顺序约束:销毁前 `_evict`/`_finalize_hand` 必须先退座位筹码回全局(`Persist(PointsWrite)`)再把人移出,顺序不能反。
- `Persist(HandRecordWrite)` 与房存亡无关,照常落库;销毁的房不再 `Broadcast`(见 [connection.md](connection.md))。

**`Disconnect` 与销毁的关系**

- 在座者只标 `OFFLINE` 保座,真正移出/销毁要等 `Cleanup` 到期或 `LeaveRoom`;观战者即时移出,可触发末人销房(0070)。
- 起身(→WATCHING)仍留在房里,不销毁。

## reduce 的结构

一句话:顶层一个 `match`,每类命令一个 helper,helper 内部一律「先校验、再改、后产事件」。

```python
def reduce(work, cmd):
    match cmd:
        case PlayerAction():   return _player_action(work, cmd)
        case StartHand():      return _start_hand(work, cmd)
        case Timeout():        return _timeout(work, cmd)
        case Connect():        return _connect(work, cmd)
        ...
```

- 每个分支先校验,不通过就返回 `Err`;再改工作副本;最后产出 events。
- 失败安全由丢弃工作副本保证(见 [error.md](error.md))。
- 每个 helper 也走 Go 风格错误:`Err | None` 或 `(value, Err)`,不 `raise`;异常只留给 bug。

## 一手牌的生命周期

### 1. 开局(`StartHand`)

校验四条:房间处于 `PENDING_START`;发起人已 `READY_TO_PLAY`;就座者中 `READY_TO_PLAY` ≥ 2;无在途 `Hand`。

然后依次执行:

1. **定庄**:`button_position` 推进到下一个发牌座位。发牌座位是 `READY_TO_PLAY` 且属于已入局/付盲/bootstrap/免盲这几类的座位;选「等大盲」的人不持庄。精确集合见 [rules.md](rules.md) ①。
2. **排座**:把就座的 ready 玩家按「庄之后→庄」的顺序排成 `players`,使 `players[0]=小盲`、`players[1]=大盲`。两人局特例:庄 = 小盲。
3. **建 Hand**:`hand_seq += 1`;`hand = Hand(status=PRE_FLOP, players, last_bet=2*small_blind, contributed={}, epoch=0, seq=room.hand_seq, start_time=cmd.started_at)`。同时锁钱:把每个 `Seat.points` 锁进 `Player.points`,存一份 `Seat.in_game_points` 快照,然后 `Seat.points=0`。
4. **下盲**:小盲投 `small_blind`,大盲投 `2*small_blind`。`bet_amount`/`last_bet`/短码 `ALLIN` 的细则见 [rules.md](rules.md) ①「下盲」。新玩家入局走「付盲即玩 / 等大盲免费」(rules.md ①),不依赖死盲记账。
5. **发牌**:洗牌用 `random.SystemRandom`(不变量 1 允许),或用 `StartHand.deck` 重放。给每人发 2 张 `hole_cards`。不烧牌:轮转取前 `2N` 张,余牌存 `hand.deck`,公共牌在街推进时从牌堆顺取。
6. **置 `PLAYING`**:参与者 UserStatus → `PLAYING`,`RoomStatus → HAND_STARTED`。本手未被发牌的在座者重标 `new_here`,它是「上一手是否参与」的判据,用来防躲盲(见 [rules.md](rules.md) ①)。
7. **定行动者**:`acting_position` = 大盲下一位,两人局为小盲/庄;`epoch=0`。

产出:`Broadcast(HandStarted)`(不含底牌)、每人一条 `Personal(HoleCards)`、`Broadcast(HandStatusChanged)`,以及 `TurnChanged` 起行动倒计时(见 [timer.md](timer.md))。

### 2. 玩家动作(`PlayerAction`)

校验三条:有 `Hand`;`acting_position` 指向发起人;动作合法。

三种动作:FOLD / CHECK / BET。BET 合并了下注、跟注、加注,`amount` 是本街目标总额。精确校验(何时可弃、何时可 check、all-in、min-raise)见 [rules.md](rules.md) ②。

改完后走下注轮推进(下一节),由它决定:换人 / 进下一街 / 摊牌 / 结束。

产出 `Broadcast(PlayerActed)`,带 pot 与下一行动者;外加推进本身带来的事件。

### 3. 下注轮推进

**本街未关闭 → 换人**:`acting_position` = 下一个 `ACTIVE`,`epoch += 1`,产出 `TurnChanged`。

**本街已关闭 → 结算本街,再按情况分支**:

- 仅剩 1 个未弃牌者 → 直接结束;`ACTIVE` ≤ 1 → 跑完公共牌进摊牌。
- 否则进 `next_status` 发公共牌:产出 `Broadcast(HandStatusChanged)` + `TurnChanged`,`epoch += 1`。
- `RIVER` 关闭 → 进 `SHOWDOWN`。

延伸:关闭谓词(`has_acted` + 匹配)、min-raise、all-in 重开、heads-up 见 [rules.md](rules.md) ②;街道结算与分支细节见 rules.md ③,带穷举测试。这是 core 最易错的地方之一。

### 4. 摊牌(`SHOWDOWN`)与结束(`ENDING`)

- **摊牌**:补齐未发的公共牌;产出 `Broadcast(HandShowDown)`,显式携带未弃牌者的 `hole_cards`。这一份不经默认隐藏的 Player 序列化;隐私边界见不变量 3。
- **分池**:用 `contributed` 算边池,用 treys 定胜负(下节)。
- **结算**:每个 `Player.points`(赢得的 + 剩余)还回 `Seat.points`,`Seat.in_game_points=0`。`PLAYING` 玩家 UserStatus → `SITTING_IN`。局中请求坐出者(`room.sitting_out_next`)转 `SITTING_OUT`。局中离桌者(`room.leaving`)不转状态,随后驱逐。**每个发生转移的人产出一条 `Broadcast(UserStatusChanged)`**,排在 `HandEnded` 之后——客户端只能从事件知道状态变了,「有座不在手需重新 ready」([connection.md](connection.md))正是靠它;0082 之前漏发,客户端一直以为大家还 ready,开不了第二手(见 [changes/0082](refactor/changes/0082-vote-config-and-hand-end-status.md))。
- **驱逐离桌者**:对 `room.leaving` 里每人,依次退座位剩余筹码回全局积分(`Persist(PointsWrite)`)、释座、移出 `users_in_room`、`del world.users`,产出 `Broadcast/Personal(UserLeft)`(见 [rules.md](rules.md) ④ / [user.md](user.md) / [lobby.md](lobby.md))。
- **落库**:产出 `Persist(HandRecordWrite)`,事件写、追加。`dedupe_key = f"{room}:{hand.seq}"`(见「手牌标识」)。`start_time = hand.start_time`;`end_time` 留空,由 shell 在派发该 `Persist` 时盖墙钟。记录内容是结果:各 participant 的 `uid`(由 `work.users[player.nickname].uid` 取)、`initial_points`/`final_points`、`final_pot`;不含底牌。
- **收尾**:`room.hand=None`,`RoomStatus → PENDING_START`,产出 `ClearAction` 停行动倒计时。

产出顺序:

1. `Broadcast(HandShowDown)`,若摊牌。
2. `Broadcast(HandEnded)`。
3. `Persist(HandRecordWrite)`。
4. 若有 `room.leaving`,逐人 `Persist(PointsWrite)` + `Broadcast/Personal(UserLeft)`。
5. `ClearAction`。

## 边池与分配(side pots)

一句话:纯函数,输入是每人本手总投入(`contributed[nick]`),输出是各池归属。

- 实现移植自 `get_winner_pot`,并修掉了它的 bug。
- 精确算法(退还未叫注 → 分层削池 → 判池归属 + 奇数零头)与测试见 [rules.md](rules.md) ③。
- treys 评估(`Evaluator.evaluate`,O(1))是 core 内的纯计算,无 IO,因此合法。`Evaluator` 单例在 core 内创建。

## 手牌标识与 staleness(定死 epoch / id)

一句话:`epoch` 判「这条超时还新鲜吗」,`seq` 判「这是哪一手」,**两者都不够,要连房名一起用**。

**`hand.epoch`(行动新鲜度)**

- 每次行动推进或街道切换自增;`Timeout` 命令携带调度时的 `epoch`。
- reduce 进门比对:`hand.epoch != cmd.epoch` 说明该回合已推进,忽略。

**`hand.seq`(手牌标识)**

- 开局时从 `room.hand_seq` 自增取得,房间内单调。
- `dedupe_key = f"{room}:{seq}"`,供 **delayDB**(异步落库层)做幂等(见 [db.md](db.md))。

**`Timeout` 的身份是三元组 `(room, hand_seq, epoch)`(0090)**,三项全等才算新鲜:

- 只比 `epoch` 会**跨手撞号**——它每手从 0 起,「上一手的第 N 回合」和「这一手的第 N 回合」长得一样(BUG-3)。
- 补上 `seq` 仍会**跨房撞号**——`seq` 只在房内单调,两个房的第 1 手同为 `seq=1`;而 `checkout` 按「他**现在**在哪」解析目标房,人换了房陈旧命令就落进新房(0072·N4)。
- `Timeout.room` **只作校验、不作路由**:目标房照旧由 `world.users[nick].room` 推定(硬规则 8 不变)。判据与流程见 [timer.md](timer.md)「过期防护」。

三者都是内存内自增计数或既有标识,由状态推导,不读墙钟、不读随机,不违反不变量 1;不引入 wall-clock 的 `hand_id`。

## 事件产出一览(A 组对外 / B 组内部)

一句话:A 组是发给客户端/落库、走队列的事件;B 组是同步交给 Timer 的事件。

| 时机 | A 组(走队列) | B 组(同步,Timer) |
|---|---|---|
| 开局 | `Broadcast(HandStarted)` + 每人 `Personal(HoleCards)` + `Broadcast(HandStatusChanged)` + `new_here` 变了的座位各一条 `Broadcast(UserStatusChanged)`(见 §1) | `TurnChanged` |
| 动作·换人 | `Broadcast(PlayerActed)` | `TurnChanged` |
| 动作·进街 | `Broadcast(PlayerActed)` + `Broadcast(HandStatusChanged)` | `TurnChanged` |
| 摊牌 | `Broadcast(HandShowDown)` | — |
| 结束 | `Broadcast(HandEnded)` + `Persist(HandRecord)` + 本手离场参与者各一份 `Personal(HandShowDown/HandEnded)`(见下) | `ClearAction` |
| 买入/离桌/起身 | `Broadcast(...)` + `Persist(PointsWrite)` | — |
| 免盲投票 | `Broadcast(FreeEntryVoteUpdated/Closed)`,三个时机是开票 / 进度 / 终结(见 [rules.md](rules.md) ①) | — |
| 房间聊天 | `Broadcast(ChatMessage)`;不改游戏状态(见 [messaging.md](messaging.md)) | — |
| 进房(JoinRoom) | `Broadcast(UserJoined)` + `Personal(StateSnapshot)`;同时把用户装入 `world.users`(见 [lobby.md](lobby.md)) | — |
| 重连(Connect,OFFLINE) | `Broadcast(UserStatusChanged)` + `Personal(StateSnapshot)`(见 [connection.md](connection.md)) | — |
| 顶替再连(Connect,在线) | `Personal(StateSnapshot)`,只对齐新连接:状态不变,不广播(见 [connection.md](connection.md)) | — |

开局那条 `UserStatusChanged` 的由来(0084):`_start_hand` 末尾要按防躲盲重标 `new_here`(被发牌者清、未被发牌的在座者置上,见 [rules.md](rules.md) ①),而这个标志此前**没有任何事件承载**——它只在 `StateSnapshot.SeatView` 里,于是客户端那份打完一手就过期,免盲开票入口无从判断(0082·A 记的缺口)。现在对**值真的变了**的座位各产一条,排在 `HandStarted`/`HoleCards` 之后(同手尾状态广播的次序:先知道这手怎么开的,再知道各座位落到什么状态)。只发变了的 ⇒ 稳态牌桌每手 0 条。

`UserStatusChanged` 因此带 `new_here: bool | None`(未就座为 `None`,与 `seat_position` 同语义),五处产出点都如实填——客户端不必、也不允许自己推断这个标志。

手尾那几份 `Personal` 的由来(0091):`Broadcast` 的收件人由 dispatch 在 **commit 之后**按 `users_in_room` 解析,而本手离场者在同一条 reduce 里已被 `_evict` 移出成员表——等到派发时他已不在名单上,于是**投了一整个底池的人看不到它是怎么分的**(BUG-10)。所以对「本手参与者 ∩ 本手末尾被驱逐者」各补一份结算结果的私发。

> **把 `_evict` 挪到广播之后不管用**:dispatch 对**整批**事件用的是同一份 commit 后的成员表,而 commit 是原子的。BUGS 里登记的那条备选修法据此作废,已更正。
> 补发只覆盖「这手怎么结的」两条(`HandShowDown`/`HandEnded`);离场者自己的 auto-fold(`PlayerActed`)是他点离开的直接结果,不补。

免盲投票同理(0088):**投票人集合变了就要补一条 `FreeEntryVoteUpdated`**(`_maybe_resolve_entry_vote`,
离场/坐出/起身/准备都会触发),而 `StateSnapshot` 也投影一份 `free_entry_vote` —— 重连与顶替只私发快照、
不重发投票事件,不投影的话面板凭空消失,全票制下最要命的一例是「重连回来的人再点 Ready 才重新成为
合格投票人,却没有任何事件说过这件事」,票就此永久卡住(BUG-9)。

同一条理由适用于**下注态**(0087):`HandStarted` 带 `pot`(开局即盲注之和,不是 0),`HandStatusChanged` 带 `last_bet` 与本街起点的 `players[]`。此前这两样都靠客户端推——「开局底池是 0」「换街了所以本街投入全清零」——而开局那条 `HandStatusChanged(PRE_FLOP)` 紧跟 `HandStarted`、盲注**已经下了**,推断在这里恰好是错的,后果是整轮 preflop 的跟注都发成 `bet_amount=0` 被 `ILLEGAL_ACTION` 拒。规则输出由服务器说,客户端只显示。

载荷约定:

- `Broadcast`/`Personal` 的 payload 是 wire `ServerMessage`,`Persist` 是 delayDB 结构。
- 两者都带快照值,不持 `world` 活引用(不变量 7,由工作副本天然保证)。

`StateSnapshot`(入桌/重连补全):

- 由 `Personal` 私发,装当前可见的整桌状态:座位与各人筹码、`button_position`、已发的公共牌、底池、`acting_position`、自己的 `hole_cards`。他人底牌不发。
- 新观战者、新入座、重连都靠它一次对齐;慢客户端被丢连后重连也走这条补回(见 [architecture.md](architecture.md) 的队列满处理)。
- 它只发快照、不改 `world`,所以重连无需回放历史事件。

## 核心不变量(core 专属)

1. **筹码守恒**:任一时刻 `Σ Player.points + Σ Player.bet_amount + Σ contributed == 开局锁入的总筹码`。三者都要算,因为 `bet_amount` 是本街尚未并入 `contributed` 的投入。结算后 `Σ 还回 Seat.points == 同一总额`。每个分支后可 `assert`,测试期开启。
2. **全局积分不在对局内流转**:下注、底池、结算只动 `Player`/`Seat`/`contributed`,不碰 `UserState`。全局积分只在 `BuyIn`/`LeaveRoom`/`Cleanup` 变动(见 [user.md](user.md))。
3. **底牌/牌堆隐私**:除 `Personal(HoleCards)` 与摊牌的 `HandShowDown` 外,任何事件、日志、落库都不含 `hole_cards`/`deck`。
4. **行动唯一**:每房间至多一个 `acting_position`;过期 `Timeout` 必被三元身份 `(room, hand_seq, epoch)` 的 staleness 校验挡掉。
5. 先校验后改是习惯;正确性兜底是工作副本 discard。
6. **一个用户只在一个房间**:`UserState.room` 记其所在房间。已在某房者 `JoinRoom` 到别房会被拒(`ALREADY_IN_ROOM`),要先 `LeaveRoom`。这保证全局积分的载入/驱逐无歧义(见 [user.md](user.md))。

## 测试(core 可纯单测)

一句话:给定 `world + 命令序列`,断言改后状态与产出的 `Event` 列表,无需 DB/WS。

必须覆盖:

- 边池(多档 all-in)、奇数池零头归属
- 入局付盲即玩 / 等大盲、大盲 preflop 选择权
- 单人未弃牌直接结束、全 all-in 跑完公共牌
- 超时默认动作(check/fold)、断线在自己回合被自动 fold
- 重连恢复

## 待定 / 高风险

- **规则以 rules.md 为准**:盲注、下注轮关闭、边池三块已在 [rules.md](rules.md) 钉死。本文「一手牌的生命周期」只是骨架。
- **wire 协议消息清单 + 底牌揭示字段**:治理见 [wire.md](wire.md),清单在 .py(未写)。core 只约定摊牌事件显式带未弃牌者底牌。
- **域模型 vs wire DTO 的物理拆分**:本文给的是 core 视角字段。wire DTO 的治理/codegen 见 [wire.md](wire.md),DTO 字段在 .py(未写)。
