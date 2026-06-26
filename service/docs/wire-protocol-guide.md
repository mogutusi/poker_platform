# wire 协议·前端对接指南

> 给前端的「怎么用」手册。治理规则见 [wire.md](wire.md)(单一事实源/演进);**字段清单永远以生成的 TS 为准,不在本文重复**(重复必漂移)。本批协议覆盖**已落地模块**(座位/买入/状态/开局/动作/摊牌/结束/离开 + 错误);其余随后端模块增量补(见末节)。

## 1. 你唯一要 import 的文件

`frontend/src/types/wire.gen.ts` —— 后端 Pydantic 自动生成的 TS 类型。

- **只 import,绝不手改**(改了下次 codegen 覆盖;后端有漂移守门测试,源码改了不重生成会红)。
- 旧的手写 `src/types/poker.ts` 是 **UI mockup 聚合类型 + 本地 mock 牌局逻辑**,不是协议——协议一律改用 `wire.gen.ts`。
- 里面有:enums(`UserStatus`/`HandStatus`/`PlayerStatus`/`PlayerActionType`/`RoomStatus`/`CardRank`/`CardSuit`/`ErrorCode`)、值对象(`Card`/`PlayerView`/`SeatView`/`ShowdownReveal`/`NickAmount`)、`ServerMessage` 联合(你收)、`ClientMessage` 联合(你发)。

## 2. 通信形状(几条铁律)

1. **每条消息带 `type` 字面量**,是可辨识联合。收到后 `switch (msg.type)` 即把 `msg` 收窄到具体类型:
   ```ts
   function onMessage(msg: ServerMessage) {
     switch (msg.type) {
       case "hand_started":   /* msg: HandStarted */ break;
       case "player_acted":   /* msg: PlayerActed */ break;
       case "error":          /* msg: ErrorMessage */ break;
       default: /* 忽略不认识的 type(向后兼容:后端会加新消息) */
     }
   }
   ```
2. **字段一律 `snake_case`**(后端同源)。别转 camelCase——转了和生成类型对不上。
3. **身份不进报文**:你发的消息**不带 nick / id**。身份由 **WS 连接(会话)** 决定,后端按连接盖。所以 `player_action` 只有 `{type, action, bet_amount?}`,没有"谁"。
4. **枚举用生成的字面量**:`"ready_to_play"` / `"fold"` / `"sitting_in"`…,不写裸字符串。
5. **忽略不认识的 `ServerMessage.type`**:协议加性演进,后端会增消息;`default` 跳过即向后兼容。

## 3. 你发什么(`ClientMessage`)

| `type` | 字段 | 语义 |
|---|---|---|
| `join_room` | `room` | 从大厅进某房(观战);后端按你的连接身份**读 DB 富化** `uid`/积分,回 `user_joined` 广播 + 私发 `state_snapshot`。失败 `error`(`NO_SUCH_ROOM`/`ALREADY_IN_ROOM`)|
| `sit_down` | `seat, wait_for_big_blind?` | 观战 → 入座该座位;`wait_for_big_blind=true`=等大盲免费入局,缺省 `false`=付盲即玩(见 rules.md ①) |
| `buy_in` | `seat, amount` | 全局积分 → 座位筹码(`amount` 为转入额) |
| `set_user_status` | `status, seat?` | `ready_to_play`/`sitting_in`/`sitting_out` 切换;`watching`=起身离座(退筹) |
| `start_hand` | `seat` | 开新一手(房内 ≥2 人 ready 时) |
| `player_action` | `action, bet_amount?` | `fold` / `check` / `bet`;**`bet` 时 `bet_amount`=本街目标总额** |
| `leave_room` | — | 退房(局中则自动弃牌,手尾结算后离座) |
| `room_chat` | `text` | 房间聊天(广播给全房,含观战者);后端文本防护把关:空→`error(INVALID_MESSAGE)`、超长→`error(MESSAGE_TOO_LONG)`、刷屏→`error(RATE_LIMITED)`(见 0033)|
| `open_free_entry_vote` | — | 为当前新玩家开一次免盲投票(有新人 + 有合格投票人时;否则回 `error`) |
| `vote_free_entry` | `approve` | 对免盲投票表态;**全体投票人 approve 才免**、任一 `false` 即失败 |
| `fetch_room_chat` | `room` | 拉该房最近 N 条房聊(进/重进房对齐历史);后端 shell 直回 `room_chat_history`(不进游戏循环,见 0036)|
| `direct_message` | `to_nick, text` | 私聊某人(跨房/大厅均可);后端 shell 路由:落库(未读)+ 对方在线即投 `dm_delivered`,不进游戏循环(见 0038)。对端不存在→`dm_undelivered`;空/超长/发给自己/刷屏→`error`(`INVALID_MESSAGE`/`MESSAGE_TOO_LONG`/`CANNOT_DM_SELF`/`RATE_LIMITED`)|
| `dm_mark_read` | `peer_nick, read_through` | 标记和 `peer_nick` 的会话已读到 `read_through`(回传你收到的 `dm_delivered.created_at`);后端落已读游标 + 对方在线即回 `dm_read`,不进游戏循环(见 0039)。对端不存在→`error(INVALID_MESSAGE)`、=自己→`error(CANNOT_DM_SELF)`|

发送:`ws.send(JSON.stringify(msg))`。非法报文/字段后端回 `error`。

> **`player_action.bet_amount` 是「本街目标总额」,不是增量**:跟注=把 `bet_amount` 设成当前 `last_bet`;加注=设成更大的目标额;all-in=设成你的全部可用额。`fold`/`check` 不带 `bet_amount`。

## 4. 你收什么(`ServerMessage`)

按 `type` 分发渲染:

| `type` | 关键字段 | 渲染 |
|---|---|---|
| `hand_started` | `players[]`(行动序快照)、`button_position`、`small_blind`/`big_blind`、`acting_position` | 开局铺桌 |
| `hole_cards` | `cards`(你**自己**的两张) | **私发本人**;摆你的手牌 |
| `hand_status_changed` | `status`(街)、`board`(已发公共牌) | 翻 flop/turn/river |
| `player_acted` | `nickname`/`action`/`bet_amount`/`points`/`status`、`pot`、`last_bet`、`acting_position` | 某人动作 + 推进后底池/下一行动位 |
| `hand_show_down` | `board`(完整 5 张)、`reveals[]`(未弃牌者底牌) | 摊牌亮牌 |
| `hand_ended` | `winnings[]`、`refunds[]` | 结算发筹码 |
| `user_status_changed` | `nickname`/`status`/`seat_position` | 谁就座/ready/坐出/离线/起身/重连 |
| `user_joined` | `nickname` | 谁进房(观战);加进房间名册 |
| `user_left` | `nickname`/`seat_position` | 谁离桌(释放座位) |
| `state_snapshot` | `seats`(仅已占座,各带 `seat_position`)/`max_seats`/`watchers`/`button_position`/`board`/`pot`/`acting_position`/`players`(行动序,不含底牌)/`your_hole_cards`(只你自己,在手才有)… | **私发**:进房/重连一次性对齐整桌;空座由 `max_seats` 渲染 |
| `room_chat_history` | `room`、`messages[]`(该房最近 N 条 `chat_message`,旧→新) | **私发**:`fetch_room_chat` 的回应,渲进聊天区 |
| `dm_delivered` | `msg_id`/`from_nick`/`text`/`created_at` | **私发**:收到一条私信(在线实时投 / 登录补收均用此形);按 `msg_id` 去重 |
| `dm_undelivered` | `to_nick` | **私发回发件人**:私信对端**根本不存在**(离线不算——离线落库后由对方登录补收) |
| `dm_read` | `reader_nick`/`read_through` | **私发回发件人**:`reader_nick` 把你发给 ta 的消息读到了 `read_through`(已读回执;在线实时 / 登录补收同形) |
| `player_bought_in` | `nickname`/`seat_position`/`amount`/`seat_points` | 谁买入、座位新筹码 |
| `free_entry_vote_updated` | `candidates`/`voters`/`approvals` | 免盲投票当前态(开票=`approvals` 空,逐票累加);给投票人显示进度/提示 |
| `free_entry_vote_closed` | `passed`/`waived` | 投票终结:`passed=true` 时 `waived` 为本手免费入局者快照,失败为空 |
| `chat_message` | `from_nick`/`text` | 房间聊天广播(`from_nick`=发言者;正文不含游戏隐私) |
| `error` | `code`(`ErrorCode`)、`detail?` | 见 §6 |

> **`acting_position` 是 `players[]` 的下标,不是座位号**:`hand_started.players` 按行动序排(`[0]`=小盲、`[1]`=大盲)。"轮到谁"= `players[acting_position]`,它的座位是 `.seat_position`。`acting_position` 为 `null` 表示无人可行动(手已结束/全 all-in)。

## 5. 一手牌的典型时序(已落地部分)

```
你 → sit_down{seat:2}                  ← user_status_changed{nick:你, status:"sitting_in", seat_position:2}
你 → buy_in{seat:2, amount:100}        ← player_bought_in{nick:你, seat_position:2, amount:100, seat_points:100}
你 → set_user_status{status:"ready_to_play"}   ← user_status_changed{..."ready_to_play"}
某人 → start_hand{seat}                ← hand_started{button_position, small_blind, big_blind, players[], acting_position}
                                       ← (私发你)hole_cards{cards:[你的两张]}
                                       ← hand_status_changed{status:"pre_flop", board:[]}
轮到你(players[acting_position]==你)→ player_action{action:"bet", bet_amount:10}
  每次有人动                            ← player_acted{nickname, action, bet_amount, points, status, pot, last_bet, acting_position}
本街关闭(自动)                        ← hand_status_changed{status:"flop", board:[3张]}  → turn[4] → river[5]
摊牌                                    ← hand_show_down{board:[5张], reveals:[未弃牌者底牌]}
                                       ← hand_ended{winnings:[{nickname,amount}], refunds:[...]}
非法操作(如非你回合 / 钱不够)         ← error{code:"NOT_YOUR_TURN", detail:"..."}
```

筹码语义:`PlayerView.points`/`PlayerActed.points` = 该玩家**本手剩余可下注筹码**;`bet_amount` = **本街已投入**;`pot` = **总底池**(已并入的 + 各人本街投入)。

## 6. 错误处理

`error` 报文 = `{ type:"error", code: ErrorCode, detail?: string }`。

- **按 `code` 映射你自己的本地化文案**(协议只回机器码,不回面向玩家的文案)。常见:`NOT_YOUR_TURN`/`ILLEGAL_ACTION`/`INSUFFICIENT_POINTS`/`SEAT_TAKEN`/`NOT_YOUR_SEAT`/`HAND_IN_PROGRESS`/`INVALID_BUY_IN`/`INVALID_STATUS_TRANSITION`…(全集见 `ErrorCode`)。
- `detail` 是**开发上下文**(谁/哪个座位/什么状态),供调试/日志,**别直接展示给玩家**。
- 错误只回**发起那条命令的连接**(不广播)。

## 7. 隐私(前端无需特别处理,但要知道)

- **别人的底牌永远不出现在广播里**(`ServerMessage` 广播类报文**结构上就没有** `hole_cards` 字段)。
- 你只在三处拿到底牌(全是「你自己 / 摊牌揭示」,绝无他人未摊的牌):`hole_cards`(**你自己**,私发)、`hand_show_down.reveals`(摊牌时未弃牌者)、`state_snapshot.your_hole_cards`(进房/重连私发,只含**你自己**的牌,在手才有否则 `null`)。据此渲染:平时只翻自己的牌,摊牌才翻对手。

## 8. 现在有 / 还没有(增量交付)

**已交付**:**进房(`join_room` ↔ `user_joined` + 私发 `state_snapshot`;后端读 DB 富化 `uid`/积分,见 0030)**、座位(`sit_down`)、买入(`buy_in`)、状态/起身(`set_user_status`)、开局(`start_hand`)、动作(`player_action`)、离开(`leave_room`)、**免盲投票(`open_free_entry_vote`/`vote_free_entry` ↔ `free_entry_vote_updated`/`free_entry_vote_closed`)**、**房间聊天(`room_chat` ↔ `chat_message`)**、**整桌快照 `state_snapshot`**(进房私发,或重连经后端 `Connect` 私发;`your_hole_cards` 只含你自己的牌)+ 上面所有其它 `ServerMessage`。

**已交付(续)**:**私聊「发」路(`direct_message` ↔ `dm_delivered` / `dm_undelivered`,见 0038)+ 「读」路·已读回执(`dm_mark_read` ↔ `dm_read`,在线实时,见 0039)**。

**还没有(随后端模块增量补到 `wire.gen.ts`,你 pull 最新生成文件即可)**:
- 大厅房间列表(REST)、私聊登录补收(连接时补发未读 `dm_delivered` + 已读回执 `dm_read`,0040)、房配置(设盲注/买入额)。

## 9. 怎么连(Phase D · 即将)

明文 dev WS 端点正在做(下一步)。落地后:`ws://<host>/dev?nick=<你的昵称>`(**开发用、明文、无加密**),连上即用上面的报文收发。正式国密加密信道放在**最后**做,对你**透明**——加解密在连接边界,你收发的始终是同样的明文 `ServerMessage`/`ClientMessage` JSON。

**现在(端点落地前)就能做的**:对着 `wire.gen.ts` 把消息类型、`switch(type)` 分发、UI 组件、桌面状态机写起来,用 mock 数据驱动;端点一通,换成真 socket 即可。
