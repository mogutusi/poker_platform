# core 游戏状态机(reduce)

## 定位

core 是**纯同步的游戏规则层**:`reduce(work, cmd) -> (list[Event], Err | None)`。它只认两个外部类型(`Command` 进、`Event`/`Err` 出),不 import 任何 FastAPI / SQLAlchemy / WebSocket 符号,不 `await`、不碰 DB、不读墙钟(不变量 1)。`reduce` 改的是 GameLoop 给的**工作副本**,成功 commit、失败/异常丢弃(见 [storage.md](storage.md))。

本文定义 core 的**域模型、命令全集、reduce 结构、一手牌的状态机**。它描述**目标行为**——原型 `gamelogic.py`/`services.py`(已于 0027 拆除,存于 git history)曾是它要取代的实现(且含若干 bug,如 `pots.values().sum()`、`hand.handstatus`、`do_action` 里循环变量覆盖入参等),不作为事实来源。

## 域模型(core 的权威状态)

core 看到的是 `world`,与 wire DTO 分离(治理见 [wire.md](wire.md))。**精确字段清单以 [`app/core/domain.py`](../app/core/domain.py) 为准**(每个字段带中文注释,代码即文档);本文只讲各实体的职责与几条关键不变量:

| 实体 | 职责 | 要点 |
|---|---|---|
| `World` | 内存权威:全部房间 + 全局用户表 | `users` 键为 nick,只装在房用户(大厅用户不进,见 [user.md](user.md)) |
| `Room` | 一个牌桌的全部状态 | 定长 `seats`、`button_position`、`hand_seq`(手牌标识);`entry_vote`/`waive_entry_for` 见免盲投票、`leaving`(局中离桌待手尾驱逐)/`sitting_out_next`(局中请求坐出待手尾生效)见局中生命周期([rules.md](rules.md) ①④) |
| `EntryVote` | 免盲投票进行态 | `approvals` 集合 + 任一 `rejected` 即失败([rules.md](rules.md) ①) |
| `Seat` | 「在桌」的钱与身份,跨手牌存活 | `points`(桌上筹码)/`in_game_points`(锁入本手的快照)/`new_here`/`wait_for_big_blind`(入局方式) |
| `Hand` | 一手牌的全部状态 | `players` 按行动序([0]=SB、[1]=BB);`contributed`(本手累计投入,旧名 pots);`last_bet`/`last_raise_size` 供下注规则;`epoch`(staleness)/`seq`(标识)/`start_time`(shell 盖、core 不读) |
| `Player` | 「在这一手里」的状态,手尾即弃 | `status`、`points`、`bet_amount`(本街)、`has_acted`([rules.md](rules.md) ②)、`hole_cards`(隐私) |

> **底牌/牌堆是隐私**:`Player.hole_cards`、`Hand.deck` 任何时候都不进日志/落库(见 [log.md](log.md));wire 上默认隐藏,只有摊牌时由专门事件显式揭示(见下)。
>
> **墙钟外移**:`Hand.start_time` 由 shell 在 `StartHand` 里盖好带入,core 只存不读、绝不据它分支(不变量 1)。

## 状态机(四套,各管一层)

| 状态机 | 取值 | 谁推进 |
|---|---|---|
| **RoomStatus** | `PENDING_START` → `HAND_STARTED` → (回到)`PENDING_START` | StartHand / 手牌结束 |
| **UserStatus** | `WATCHING`/`SITTING_IN`/`READY_TO_PLAY`/`SITTING_OUT`/`PLAYING`/`OFFLINE` | 玩家操作 + 连接生命周期;合法转移表见 [enums.py](../app/core/enums.py) |
| **HandStatus** | `PRE_FLOP` → `FLOP` → `TURN` → `RIVER` → `SHOWDOWN` → `ENDING` | 下注轮关闭(`next_status`) |
| **PlayerStatus** | `ACTIVE` / `FOLDED` / `ALLIN` | 玩家动作 |

`UserStatus` 与 `PlayerStatus` 正交:前者是「这个人在房间里的身份」(观战/就座/准备/在玩/离线),后者是「这一手里这个座位的牌局状态」。开局把 `READY_TO_PLAY` 的人变 `PLAYING`,结束变回 `SITTING_IN`。**所有 UserStatus 转移必须查 `USER_STATUS_TRANSITIONS` 合法表**,非法转移 `return [], Err(...)`。

## Command 全集(开放集合)

**模型 2 红利:游戏命令不带 `room`。** 用户同时只在一个房间,目标房 = `world.users[nick].room`,reduce 和 `checkout` 据此解析(只有 `JoinRoom` 例外:目标房在命令里)。`origin` 退成 `nick`(连接绑 nick,见 [connection.md](connection.md))。

| Command | origin | 来源 | 语义 |
|---|---|---|---|
| `JoinRoom(room, uid, loaded, create?)` | nick | wire | 大厅→房间;`uid`/`loaded` 为 DB 读出的账号主键与积分;**房不存在则动态建房**(谁都可创建,见「房间生命周期」/0049:用 shell 盖的 `create` 配置建空房再加入;`create=None` 且房不存在 → `NO_SUCH_ROOM`)/未在别房(`ALREADY_IN_ROOM`),装入 `world.users` 为 WATCHING(`ROOM_FULL` v1 不强制,见 [lobby.md](lobby.md))。core 已落地(进房 0022 / 建房 0049);client 报文 `join_room{room}`(建房配置不进报文,shell 盖) |
| `LeaveRoom()` | nick | wire | 退分离桌,回大厅;驱逐出 `world.users` |
| `SitDown(seat, wait_for_big_blind)` | nick | wire | 观战→入座;`wait_for_big_blind` 声明入局方式(等大盲免费 / 默认付盲即玩,见 [rules.md](rules.md) ①) |
| `BuyIn(seat, amount)` | nick | wire | 全局积分→座位筹码 |
| `SetUserStatus(status, seat)` | nick | wire | ready / sit-out / 起身等 UserStatus 转移 |
| `SetSmallBlind(amount)` / `SetBuyIn(amount)` | nick | wire | **任何在房成员**配置房间参数(无房主,0044;大盲 = 2×小盲派生);仅两手之间(`HAND_IN_PROGRESS` 拒局中,correctness 非授权)、上下限由 shell 按 `gameconfig.MIN/MAX_*` 防护(`INVALID_SMALL_BLIND`/`INVALID_BUY_IN`),产 `Broadcast(RoomConfigChanged)` 全房对齐、**不落库**(房状态不持久,见 [storage.md](storage.md))。core 已落地(0043;0044 去房主) |
| `StartHand(seat, started_at, deck?)` | nick | wire | 开新一手;`started_at`(墙钟)由 shell 盖好带入,`deck` 可选(重放用,见下) |
| `PlayerAction(action, bet_amount?)` | nick | wire | fold / check / bet |
| `RoomChat(text)` | nick | wire | 房间聊天;只读命令、不改游戏状态,产出 `Broadcast(ChatMessage)`(私聊不走这里,见 [messaging.md](messaging.md)) |
| `OpenFreeEntryVote()` | nick | wire | 有 `new_here` 时开一次免盲投票(见 [rules.md](rules.md)) |
| `VoteFreeEntry(approve)` | nick | wire | 对免盲投票表态;已入局玩家全票 `approve` 则新玩家免费入局 |
| `Connect(nick)` | None | shell | 握手后接入**大厅**(不带 room/积分)。按 world 分三类:nick 在房且 `OFFLINE`→重连恢复 + 广播 + 私发 `Personal(StateSnapshot)`;在房且在线→顶替再连,只私发快照对齐(状态不变、不广播);不在 `world.users`→纯大厅,core 无事(见 [connection.md](connection.md)) |
| `Disconnect(nick)` | None | shell | ws 断开;在房则标 `OFFLINE` 保座,在大厅则无 world 变化 |
| `Timeout(nick, epoch)` | None | Timer | 行动超时(目标房由 `world.users[nick].room` 定) |
| `Cleanup(nick)` | None | Timer | 占座到期清理 |

`origin` 决定**错误回发给谁**(见 [error.md](error.md));系统命令 `origin=None`,失败只落日志。客户端命令与 wire `ClientMessage` 1:1,但**系统命令没有报文**。`JoinRoom`/`LeaveRoom` 的房间生命周期细节见 [lobby.md](lobby.md)。

## 房间生命周期(创建 / 销毁)

- **动态房(0049,唯一模型)**:**无静态预置房——谁都可创建、空则销毁**。用户明示的设计;所有房都动态。
- **创建**:`JoinRoom` 到不存在的房时 `checkout(world, cmd)` 给「无此房间」的副本(`work.room is None`),reduce 用 `cmd.create`(`RoomCreate{small_blind,buy_in,seats}`,由 shell 从 `gameconfig` 盖——core 不 import config)在副本上新建 `Room`(空座、`PENDING_START`)再加入用户(WATCHING),`commit` 插回 `world.rooms`(见 [storage.md](storage.md))。**创建者无特权**(peer / 无房主,同 0044);建后任何在房成员可 `SetSmallBlind`/`SetBuyIn` 调参。`create=None` 且房不存在 → `NO_SUCH_ROOM`(防御:shell 应总带 `create`)。
- **销毁**:**最后一人离开房间时销毁**。`reduce()` 顶层一处归一守「已提交的房永不为空」:任一成功命令后若目标房 `users_in_room` 变空 → 置 `work.room=None` → `commit` 销毁。覆盖 `LeaveRoom` / `Cleanup` / **手尾 `_finalize_hand` 驱逐**所有清空路径。销毁前 `_evict`/`_finalize_hand` 已先退座位筹码回全局(`Persist(PointsWrite)`)再移出,顺序不反;`Persist(HandRecordWrite)` 与房存亡无关照常落库;**销毁的房不再 `Broadcast`**(见 [connection.md](connection.md))。
- `Disconnect` **不**移出房间、不销毁(只标 `OFFLINE`、保留座位);起身(→WATCHING)留房不销毁;真正的移出/销毁等 `Cleanup` 到期或 `LeaveRoom`。

## reduce 的结构

顶层按命令类型 `match`,每个分支:**先校验(返回 `Err`)→ 改工作副本 → 产出 events**。校验前置是好习惯(清晰),但失败安全由「丢弃工作副本」保证(见 [error.md](error.md))。

```python
def reduce(work, cmd):
    match cmd:
        case PlayerAction():   return _player_action(work, cmd)
        case StartHand():      return _start_hand(work, cmd)
        case Timeout():        return _timeout(work, cmd)
        case Connect():        return _connect(work, cmd)
        ...
```

每个 helper 也走 Go 风格错误(`Err | None` / `(value, Err)`),绝不 `raise`(异常只留给 bug)。

## 一手牌的生命周期

### 1. 开局(`StartHand`)

校验:房间 `PENDING_START`、发起人已 `READY_TO_PLAY`、就座者中 `READY_TO_PLAY` ≥ 2、无在途 `Hand`。然后:

1. **定庄**:`button_position` 推进到下一个**发牌座位**(`READY_TO_PLAY` 的已入局/付盲/bootstrap/免盲座位;选「等大盲」者不持庄,精确集合见 [rules.md](rules.md) ①)。
2. **排座**:把就座的 ready 玩家按「庄之后→庄」顺序排成 `players`,使 `players[0]=小盲`、`players[1]=大盲`(两人局特例:庄=小盲)。
3. **建 Hand**:`hand_seq += 1`;`hand = Hand(status=PRE_FLOP, players, last_bet=2*small_blind, contributed={}, epoch=0, seq=room.hand_seq, start_time=cmd.started_at)`(`start_time` 是 shell 带入的墙钟值,core 不读时钟)。把每个 `Seat.points` 锁进 `Player.points`、并存 `Seat.in_game_points` 快照,`Seat.points=0`。
4. **下盲**:小盲投 `small_blind`、大盲投 `2*small_blind`(新玩家入局「付盲即玩 / 等大盲」见下);更新各自 `bet_amount`(本街投入;街结束才并入 `contributed`,见 [rules.md](rules.md) ②/③);归零筹码者置 `ALLIN`。
5. **发牌**:洗牌(`random.SystemRandom`,不变量 1 允许;或用 `StartHand.deck` 重放),给每人发 2 张 `hole_cards`(**不烧牌**:轮转取前 `2N` 张,余牌存 `hand.deck`,公共牌在街推进时从牌堆顺取)。
6. **置 `PLAYING`**:参与者 UserStatus → `PLAYING`,`RoomStatus → HAND_STARTED`;**本手未被发牌的在座者重标 `new_here`**(防躲盲「上一手是否参与」,见 [rules.md](rules.md) ①)。
7. **定行动者**:`acting_position` = 大盲下一位(两人局为小盲/庄),`epoch=0`。

产出:`Broadcast(HandStarted)`(不含底牌)、每人 `Personal(HoleCards)`、`Broadcast(HandStatusChanged)`、`TurnChanged`(起行动倒计时,见 [timer.md](timer.md))。

> **新玩家入局**:精确规则见 [rules.md](rules.md)——「**付盲即玩 / 等大盲免费**」二选一(投个大盲马上玩,或不付而等大盲),既好用又防躲盲;不依赖死盲记账,`new_player_seat_list` 不需要。

### 2. 玩家动作(`PlayerAction`)

校验:有 `Hand`、`acting_position` 指向发起人、动作合法。三种动作:

- **FOLD**:仅当 `bet_amount < last_bet`(有注要跟才允许弃;无注该 check);置 `FOLDED`。
- **CHECK**:仅当 `bet_amount == last_bet`(无人加注或已跟平)。
- **BET**(下注/跟注/加注,合并为一个动作 + 金额):`amount` 是**本街目标总额**。校验不超过 `points+bet_amount`;等于则 `ALLIN`;`< last_bet` 仅允许 all-in;`> last_bet` 即加注、更新 `last_bet`。

改完后调用**下注轮推进**(下一节)决定:换人 / 进下一街 / 摊牌 / 结束。
产出:`Broadcast(PlayerActed)`(带 pot、下一行动者),以及推进带来的事件。

### 3. 下注轮推进

从 `acting_position` 起找下一个 `ACTIVE` 玩家:

- **有下一个 ACTIVE 且本街未跟平** → 换人:`acting_position = next`、`epoch += 1`、产出 `TurnChanged`。
- **本街已关闭**(所有未弃牌者 `bet_amount==last_bet`,且大盲 preflop 的选择权已用掉) → **结算本街**:各 `Player.bet_amount` 并入 `contributed` 并清零,`last_bet=0`,然后:
  - 仅剩 1 个未弃牌者 → **直接结束**(无需摊牌)。
  - `≤1` 个 `ACTIVE`(其余 all-in)→ **跑完剩余公共牌直接摊牌**。
  - 否则进 `next_status`:发该街公共牌(flop 3 / turn 1 / river 1),`acting_position` 回到庄后第一个 ACTIVE,`epoch += 1`,产出 `Broadcast(HandStatusChanged)` + `TurnChanged`。
  - `RIVER` 关闭 → `SHOWDOWN`。

> 关闭判据是 core 最易错处之一——**精确谓词(`has_acted` + 匹配)、min-raise、all-in 重开、heads-up 全在 [rules.md](rules.md) ②,带穷举测试**。

### 4. 摊牌(`SHOWDOWN`)与结束(`ENDING`)

- **摊牌**:补齐未发的公共牌;产出 `Broadcast(HandShowDown)`——**这是底牌唯一的合法公开点**,消息显式携带未弃牌者的 `hole_cards`(不经默认隐藏的 Player 序列化)。
- **分池**:`contributed` 算边池 + treys 定胜负(下节)。
- **结算**:每个 `Player.points`(赢得的 + 剩余)还回 `Seat.points`,`Seat.in_game_points=0`;`PLAYING` 玩家 UserStatus → `SITTING_IN`(局中请求坐出者 `room.sitting_out_next` → `SITTING_OUT`;局中离桌者 `room.leaving` 不转状态、随后驱逐)。
- **驱逐离桌者**:对 `room.leaving` 里每人,退其座位剩余筹码回全局积分(`Persist(PointsWrite)`)、释座、移出 `users_in_room` + `del world.users`,产 `Broadcast/Personal(UserLeft)`(见 [rules.md](rules.md) ④ / [user.md](user.md) / [lobby.md](lobby.md))。
- **落库**:产出 `Persist(HandRecordWrite)`(事件写,追加),`dedupe_key = f"{room}:{hand.seq}"`(见「手牌标识」)。`start_time = hand.start_time`(开局带入的值);`end_time` 留空,由 shell 在派发该 `Persist` 时盖墙钟(core 不读时钟)。记录存**结果**(各 participant 的 `uid`(由 `work.users[player.nickname].uid` 取)+ `initial_points`/`final_points` + `final_pot`),**不含底牌**。
- **收尾**:`room.hand=None`、`RoomStatus → PENDING_START`、产出 `ClearAction`(停行动倒计时)。

产出顺序:`Broadcast(HandShowDown)`(若摊牌)→ `Broadcast(HandEnded)` → `Persist(HandRecordWrite)` →(若有 `room.leaving` 离桌者:逐人 `Persist(PointsWrite)` + `Broadcast/Personal(UserLeft)` 驱逐,见上「驱逐离桌者」)→ `ClearAction`。

## 边池与分配(side pots)

`contributed[nick]` = 每人本手总投入。**精确算法(退还未叫注 → 分层削池 → 判池归属 + 奇数零头)+ 测试见 [rules.md](rules.md) ③**;以下为概览(纯函数,移植自 `get_winner_pot` 并去 bug):

1. 按投入额从小到大分**层**:每个投入档位削出一个子池,子池金额 = 该档位差 × 仍在该档的人数。
2. 每个子池的**有资格者** = 投入达到该档且**未弃牌**的玩家;胜者 = 其中 treys 牌力最强(`Evaluator.evaluate`,O(1));平分,**奇数零头给最接近庄家左手的人**(`(seat-button)%seat_size` 最小)。
3. 弃牌者的投入仍计入它够到的子池(只是没资格赢)。

treys 评估只在 core 内做纯计算(无 IO),合法。`Evaluator` 单例在 core 内创建。

## 手牌标识与 staleness(定死 epoch / id)

- **`hand.epoch`**(行动新鲜度):每次行动推进 / 街道切换自增。`Timeout` 命令携带调度时的 `epoch`;reduce 进门比对 `hand.epoch != cmd.epoch` 则该超时已过期、忽略。**这就是 [timer.md](timer.md) 里待定的「新鲜度判据」——落在 `hand.epoch`**。
- **`hand.seq`**(手牌标识):开局自 `room.hand_seq` 自增取得,房间内单调。`dedupe_key = f"{room}:{seq}"` 供 delayDB 幂等(见 [db.md](db.md))。

两者都是**内存内自增计数**,由状态推导,不读墙钟/随机,**不违反不变量 1**。**不引入 wall-clock 的 `hand_id`**。

> **墙钟外移**:`Hand.start_time` 不是 core 读出来的——它由 shell 在构造 `StartHand` 时盖好、随命令带入(同 `deck` 的外移办法),core 只是携带它。手牌记录的 `end_time` 由 shell 在派发 `Persist(HandRecordWrite)` 那一刻盖墙钟。**core 全程不读时钟**(不变量 1)。

## 事件产出一览(A 组对外 / B 组内部)

| 时机 | A 组(走队列) | B 组(同步,Timer) |
|---|---|---|
| 开局 | `Broadcast(HandStarted)` + 每人 `Personal(HoleCards)` + `Broadcast(HandStatusChanged)` | `TurnChanged` |
| 动作·换人 | `Broadcast(PlayerActed)` | `TurnChanged` |
| 动作·进街 | `Broadcast(PlayerActed)` + `Broadcast(HandStatusChanged)` | `TurnChanged` |
| 摊牌 | `Broadcast(HandShowDown)` | — |
| 结束 | `Broadcast(HandEnded)` + `Persist(HandRecord)` | `ClearAction` |
| 买入/离桌/起身 | `Broadcast(...)` + `Persist(PointsWrite)` | — |
| 免盲投票 | `Broadcast(FreeEntryVoteUpdated/Closed)`(开票/进度/终结;见 [rules.md](rules.md) ①) | — |
| 房间聊天 | `Broadcast(ChatMessage)`(只读命令,不改状态;见 [messaging.md](messaging.md)) | — |
| 进房(JoinRoom) | `Broadcast(UserJoined)` + `Personal(StateSnapshot)`(装 `world.users`、见 [lobby.md](lobby.md)) | — |
| 重连(Connect,OFFLINE) | `Broadcast(UserStatusChanged)` + `Personal(StateSnapshot)`(OFFLINE→恢复,见 [connection.md](connection.md)) | — |
| 顶替再连(Connect,在线) | `Personal(StateSnapshot)`(只对齐新连接,状态不变、不广播,见 [connection.md](connection.md)) | — |

`Broadcast`/`Personal` 的 payload 是 wire `ServerMessage`;`Persist` 是 delayDB 结构。两者都带快照值,不持 `world` 活引用(不变量 7,由工作副本天然保证)。

> **`StateSnapshot`(入桌/重连补全)**:`Personal` 私发给该连接,装当前可见的整桌状态——座位与各人筹码、`button_position`、已发的公共牌、底池、`acting_position`、自己的 `hole_cards`(他人底牌不发)。新观战者/新入座/重连都靠它一次对齐;慢客户端被丢连后重连也走这条补回(见 [architecture.md](architecture.md) 的队列满处理)。它只发快照、不改 `world`,所以重连无需"回放历史事件"。

## 核心不变量(core 专属)

1. **筹码守恒**:任一时刻 `Σ Player.points + Σ Player.bet_amount + Σ contributed == 开局锁入的总筹码`(`bet_amount` 是本街尚未并入 `contributed` 的投入,街结束才并入,所以三者都要算);结算后 `Σ 还回 Seat.points == 同一总额`。每个分支后可 `assert`(测试期开)。
2. **全局积分不在对局内流转**:下注、底池、结算只动 `Player`/`Seat`/`contributed`,**绝不碰 `UserState`**;全局积分只在 `BuyIn`/`LeaveRoom`/`Cleanup` 变动(见 [user.md](user.md))。
3. **底牌/牌堆隐私**:除 `Personal(HoleCards)` 与摊牌的 `HandShowDown` 外,任何事件/日志/落库都不含 `hole_cards`/`deck`。
4. **行动唯一**:每房间至多一个 `acting_position`;`epoch` 单调,过期 `Timeout` 必被 staleness 挡掉。
5. **先校验后改**为好习惯;正确性兜底是工作副本 discard。
6. **一个用户只在一个房间**:`UserState.room` 记其所在房间;已在某房者 `JoinRoom` 到别房(已在 `world.users`)被 reduce 拒掉(`ALREADY_IN_ROOM`,要先 `LeaveRoom`)。这保证全局积分的载入/驱逐无歧义(见 [user.md](user.md))。

## 测试(core 可纯单测)

给定 `world + 命令序列`,断言改后状态 + 产出的 `Event` 列表,无需 DB/WS。**必须覆盖**:边池(多档 all-in)、入局付盲即玩/等大盲、大盲 preflop 选择权、单人未弃牌直接结束、全 all-in 跑完公共牌、超时默认动作(check/fold)、断线在自己回合被自动 fold、奇数池零头归属、重连恢复。

## 待定 / 高风险

- **盲注/下注轮关闭/边池** 三块已在 **[rules.md](rules.md)** 钉死(含 heads-up、preflop 大盲选择权、all-in 链、边池分层、奇数零头 + 穷举测试用例)。本文「一手牌的生命周期」给骨架,精确规则以 rules.md 为准:新玩家入局用「付盲即玩 / 等大盲免费」、关闭判据用 `has_acted` 谓词。
- **wire 协议消息清单 + 底牌揭示字段**:治理见 [wire.md](wire.md),清单在 .py(未写);core 只约定「摊牌事件显式带未弃牌者底牌」。
- **域模型 vs wire DTO 的物理拆分**:本文给了 core 视角字段;wire DTO 的治理/codegen 见 [wire.md](wire.md),DTO 字段在 .py(未写)。
