# wire 协议·前端对接指南

> 给前端的「怎么用」手册。治理规则见 [wire.md](wire.md)。
>
> 字段清单永远以生成的 TS 为准,不在本文重复。
>
> 本批协议覆盖已落地模块:座位、买入、状态、开局、动作、摊牌、结束、离开,加上错误。其余随后端模块增量补(见末节)。

## 1. 你唯一要 import 的文件

一句话:只 import `frontend/src/types/wire.gen.ts`,别的都不是协议。

- 它是后端 Pydantic 自动生成的 TS 类型:只 import,绝不手改——改了下次 codegen 会覆盖,而且后端有漂移守门测试。
- 旧的手写 `src/types/poker.ts` 不是协议,是 UI mockup 聚合类型 + 本地 mock 牌局逻辑;协议一律改用 `wire.gen.ts`。

`wire.gen.ts` 里有四类东西:

| 类别 | 内容 |
|---|---|
| enums | `UserStatus`/`HandStatus`/`PlayerStatus`/`PlayerActionType`/`RoomStatus`/`CardRank`/`CardSuit`/`ErrorCode` |
| 值对象 | `Card`/`PlayerView`/`SeatView`/`ShowdownReveal`/`NickAmount` |
| `ServerMessage` 联合 | 你收 |
| `ClientMessage` 联合 | 你发 |

## 2. 通信形状(几条铁律)

一句话:带 `type` 的可辨识联合、snake_case、身份不进报文、不认识的消息直接跳过。

1. 每条消息带 `type` 字面量,是可辨识联合。收到后 `switch (msg.type)` 即把 `msg` 收窄到具体类型:
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
2. 字段一律 `snake_case`,与后端同源。别转 camelCase,转了和生成类型对不上。
3. 身份不进报文:你发的消息不带 nick / id,身份由 WS 连接(会话)决定、后端按连接盖。所以 `player_action` 只有 `{type, action, bet_amount?}`,没有「谁」。
4. 枚举用生成的字面量(`"ready_to_play"` / `"fold"` / `"sitting_in"`…),不写裸字符串。
5. 忽略不认识的 `ServerMessage.type`:协议加性演进,`default` 跳过即向后兼容。

## 3. 你发什么(`ClientMessage`)

一句话:15 种客户端报文,先看表认字段,细则在表下面。

| `type` | 字段 | 语义 |
|---|---|---|
| `join_room` | `room` | 从大厅进某房(观战) |
| `sit_down` | `seat, wait_for_big_blind?` | 观战 → 入座 |
| `buy_in` | `seat, amount` | 全局积分 → 座位筹码(`amount` 为转入额) |
| `set_user_status` | `status, seat?` | `ready_to_play`/`sitting_in`/`sitting_out` 切换;`watching` = 起身离座,座位筹码退回全局积分 |
| `set_small_blind` | `amount` | 改房间小盲 |
| `set_buy_in` | `amount` | 改房间默认买入额 |
| `start_hand` | `seat` | 开新一手(房内 ≥2 人 ready 时) |
| `player_action` | `action, bet_amount?` | `fold` / `check` / `bet` |
| `leave_room` | — | 退房;局中退房则自动弃牌,手尾结算后离座 |
| `room_chat` | `text` | 房间聊天(广播全房,含观战者) |
| `open_free_entry_vote` | — | 为当前新玩家开一次免盲投票 |
| `vote_free_entry` | `approve` | 对免盲投票表态;全体投票人 approve 才免,任一 `false` 即失败 |
| `fetch_room_chat` | `room` | 拉该房最近 N 条房聊 |
| `direct_message` | `to_nick, text` | 私聊某人(跨房/大厅均可) |
| `dm_mark_read` | `peer_nick, read_through` | 标记和 `peer_nick` 的会话已读到某条 |

发送:`ws.send(JSON.stringify(msg))`。非法报文/字段后端回 `error`。

**细则**

`join_room`

- 后端按连接身份读 DB 富化 `uid`/积分,回 `user_joined` 广播 + 私发 `state_snapshot`。
- 房不存在则自动创建,这就是「动态房」:房间不预先配置,谁发 `join_room` 谁就把它建出来(0049);无特权,谁都可建、没有房主,最后一人离开即销毁。
- 新房参数用服务端默认;建好后任何成员都可以 `set_small_blind`/`set_buy_in` 改。
- 已在别房 → `error(ALREADY_IN_ROOM)`,得先 `leave_room`;`NO_SUCH_ROOM` 是后端防御,正常流程不会见到。

`sit_down`

- `wait_for_big_blind=true` = 等大盲免费入局:坐下后等到自己该交大盲的那一手才开始打,期间不掏盲注。
- 缺省 `false` = 付盲即玩:立刻补一份盲注,当手就能入局(见 rules.md ①)。

`set_small_blind`

- 任何在房成员都能改,含观战者;房间无房主。大盲 = 2× 小盲,自动派生。
- 仅两手之间可改,改后广播 `room_config_changed`;局中 → `error(HAND_IN_PROGRESS)`,越界 → `error(INVALID_SMALL_BLIND)`(见 0043 / 0044)。

`set_buy_in`

- 任何在房成员可改,规则同 `set_small_blind`;越界 → `error(INVALID_BUY_IN)`(见 0043 / 0044)。

`room_chat`

- 空 → `error(INVALID_MESSAGE)`;超长 → `error(MESSAGE_TOO_LONG)`;刷屏 → `error(RATE_LIMITED)`(见 0033)。

`open_free_entry_vote`

- 「免盲投票」= 桌上老玩家投票决定,是否让刚进来的新玩家不掏盲注就入局。需要同时有新人和有合格投票人,否则回 `error`。

`fetch_room_chat`

- 用途是进房/重进房时对齐聊天历史;后端直回 `room_chat_history`,不进游戏循环。
- 历史随房间销毁而消失,同名新房是全新历史(0071);房不存在 → 空。

`direct_message`

- 后端 shell 路由,不进游戏循环(见 0038):落库标为未读;对方在线就立刻投 `dm_delivered`,对端不存在 → `dm_undelivered`。
- 空 → `error(INVALID_MESSAGE)`;超长 → `error(MESSAGE_TOO_LONG)`;发给自己 → `error(CANNOT_DM_SELF)`;刷屏 → `error(RATE_LIMITED)`。

`dm_mark_read`

- `read_through` 回传你收到的 `dm_delivered.created_at`;后端落已读游标,对方在线即回 `dm_read`(见 0039)。
- 对端不存在 → `error(INVALID_MESSAGE)`;`peer_nick` 是自己 → `error(CANNOT_DM_SELF)`。

> **`player_action.bet_amount` 是「本街目标总额」,不是增量:**跟注 = 设成当前 `last_bet`,加注 = 设成更大的目标额,all-in = 设成你的全部可用额;`fold`/`check` 不带 `bet_amount`。

## 4. 你收什么(`ServerMessage`)

一句话:按 `type` 分发渲染;广播类给全房,私发类只给你一个人。

| `type` | 关键字段 | 渲染 |
|---|---|---|
| `hand_started` | `players[]`(行动序快照)、`button_position`、`small_blind`/`big_blind`、`acting_position`、`pot`、`last_bet`、`min_raise_to` | 开局铺桌。**`pot` 不是 0**:盲注已经下了,它就是盲注之和(0087)|
| `hole_cards` | `cards`(你自己的两张) | 私发本人;摆你的手牌 |
| `hand_status_changed` | `status`(街)、`board`(已发公共牌)、`last_bet`、`min_raise_to`、`players[]` | 翻 flop/turn/river。**本街下注态照抄,别自己推「换街了所以清零」**:开局那条 `status:"pre_flop"` 紧跟 `hand_started`,盲注**已经在桌上**(0087)|
| `player_acted` | `nickname`/`action`/`bet_amount`/`points`/`status`、`pot`、`last_bet`、`min_raise_to`、`acting_position` | 某人动作 + 推进后底池/下一行动位/加注下限 |
| `hand_show_down` | `board`(完整 5 张)、`reveals[]`(未弃牌者底牌) | 摊牌亮牌 |
| `hand_ended` | `winnings[]`、`refunds[]` | 结算发筹码 |
| `user_status_changed` | `nickname`/`status`/`seat_position`/`new_here` | 谁就座/ready/坐出/离线/起身/重连;`new_here`=该座位下一手是否仍需付入局费(未就座为 `null`),**开局末尾重标时也会补发**(0084)|
| `user_joined` | `nickname` | 谁进房(观战);加进房间名册 |
| `user_left` | `nickname`/`seat_position` | 谁离桌(释放座位) |
| `state_snapshot` | `seats`/`max_seats`/`watchers`/`button_position`/`small_blind`/`big_blind`/`buy_in`/`board`/`pot`/`last_bet`/`min_raise_to`/`acting_position`/`players`/`your_hole_cards`/`free_entry_vote`… | 私发:进房/重连一次性对齐整桌。`seats` 仅已占座、各带 `seat_position`,空座由 `max_seats` 渲染;`players` 按行动序、不含底牌;`your_hole_cards` 只你自己的牌,在手才有。一次带齐当前注码与买入额,所以进房/重连不用再补拉 |
| `room_config_changed` | `small_blind`/`big_blind`/`buy_in` | 某在房成员改了房间参数;带的是完整当前配置快照,不是差量(见 0043/0044) |
| `room_chat_history` | `room`、`messages[]` | 私发:`fetch_room_chat` 的回应,渲进聊天区;`messages[]` 是该房最近 N 条 `chat_message`,顺序旧→新 |
| `dm_delivered` | `msg_id`/`from_nick`/`text`/`created_at` | 私发:收到一条私信。在线实时投递与登录后补收离线消息同形,按 `msg_id` 去重 |
| `dm_undelivered` | `to_nick` | 私发回发件人:对端账号根本不存在。对端离线不算——离线消息会落库,由对方登录时补收 |
| `dm_read` | `reader_nick`/`read_through` | 私发回发件人:已读回执;实时与补收同形 |
| `player_bought_in` | `nickname`/`seat_position`/`amount`/`seat_points` | 谁买入、座位新筹码 |
| `free_entry_vote_updated` | `candidates`/`voters`/`approvals` | 免盲投票当前态,给投票人显示进度;开票时 `approvals` 为空,之后逐票累加 |
| `free_entry_vote_closed` | `passed`/`waived` | 投票终结;`passed=true` 时 `waived` 是本手免费入局者快照,失败时为空 |
| `chat_message` | `from_nick`/`text` | 房间聊天广播(正文不含游戏隐私) |
| `error` | `code`(`ErrorCode`)、`detail?` | 见 §6 |

> **`acting_position` 是 `players[]` 的下标,不是座位号。**
>
> - `hand_started.players` 按行动序排:`[0]` = 小盲,`[1]` = 大盲。
> - `hand_status_changed.players` 是**本街起点**的同款快照(开局带盲注,换街已归零)。

> **`min_raise_to` 是「自愿加注的合法下限」,直接用,别自己套公式。**
>
> - 规则是 `last_bet + max(last_raise_size, BB)`([rules.md](rules.md) ②),但 `last_raise_size` **不上 wire**:
>   给你公式的原料等于请你重算一遍规则,而客户端重算规则正是本仓反复出事的地方(0084 / 0087 / BUG-19)。
> - 服务端校验用的就是这同一个数(`betting.min_raise_target`),所以广播的下限与判定的下限不可能分叉。
> - **all-in 不受它限制**:筹码不够时把本街目标总额设成自己的全部即可,即便低于 `min_raise_to` 也合法。
> - 进新街后 `last_bet=0`、`last_raise_size` 重置为 BB ⇒ `min_raise_to` = BB。
> - 「轮到谁」= `players[acting_position]`,它的座位是 `.seat_position`;为 `null` 表示无人可行动,即手已结束或全员 all-in。

> **聊天正文的表情是 `[code]` 文本 token**,房聊与私聊同规则,后端纯透传(见 0034/0035)。
>
> - 渲染:按 `wire.gen.ts` 里的 `EMOJI_CATALOG` 把 `[thumbs_up]` 换成 glyph,`frontend/src/utils/emoji.ts` 的 `tokenizeChat` 已提供;不认识的 code 原样显示,这是向后兼容。
> - 发送侧插表情就是往 `text` 里拼 `[code]`,无新协议字段。

## 5. 一手牌的典型时序(已落地部分)

一句话:坐下 → 买入 → ready → 开局 → 逐街行动 → 摊牌 → 结算。

```
你 → sit_down{seat:2}                  ← user_status_changed{nick:你, status:"sitting_in", seat_position:2}
你 → buy_in{seat:2, amount:100}        ← player_bought_in{nick:你, seat_position:2, amount:100, seat_points:100}
你 → set_user_status{status:"ready_to_play"}   ← user_status_changed{..."ready_to_play"}
某人 → start_hand{seat}                ← hand_started{button_position, small_blind, big_blind, players[], acting_position}
                                       ← (私发你)hole_cards{cards:[你的两张]}
                                       ← hand_status_changed{status:"pre_flop", board:[], last_bet:BB, players:[带盲注的快照]}
轮到你(players[acting_position]==你)→ player_action{action:"bet", bet_amount:10}
  每次有人动                            ← player_acted{nickname, action, bet_amount, points, status, pot, last_bet, acting_position}
本街关闭(自动)                        ← hand_status_changed{status:"flop", board:[3张], last_bet:0, players:[本街投入已归零]}  → turn[4] → river[5]
摊牌                                    ← hand_show_down{board:[5张], reveals:[未弃牌者底牌]}
                                       ← hand_ended{winnings:[{nickname,amount}], refunds:[...]}
非法操作(如非你回合 / 钱不够)         ← error{code:"NOT_YOUR_TURN", detail:"..."}
```

筹码语义:`PlayerView.points` / `PlayerActed.points` = 该玩家本手剩余可下注筹码;`bet_amount` = 本街已投入;`pot` = 总底池,即已并入的部分加上各人本街投入。

## 6. 错误处理

一句话:`error` 只给机器码,文案你自己映射。

`error` 报文 = `{ type:"error", code: ErrorCode, detail?: string }`。

- 按 `code` 映射你自己的本地化文案。常见 code:`NOT_YOUR_TURN`/`ILLEGAL_ACTION`/`INSUFFICIENT_POINTS`/`SEAT_TAKEN`/`NOT_YOUR_SEAT`/`HAND_IN_PROGRESS`/`INVALID_BUY_IN`/`INVALID_STATUS_TRANSITION`…全集见 `ErrorCode`。
- `detail` 是开发上下文,供调试/日志用,别直接展示给玩家。
- 错误只回发起那条命令的连接,不广播。

## 7. 隐私(前端无需特别处理,但要知道)

一句话:平时只翻自己的牌,摊牌才翻对手。

别人的底牌永远不出现在广播里——广播类报文结构上就没有 `hole_cards` 字段。你只在三处拿到底牌:

| 来源 | 含谁的牌 |
|---|---|
| `hole_cards` | 你自己,私发 |
| `hand_show_down.reveals` | 摊牌时未弃牌者 |
| `state_snapshot.your_hole_cards` | 进房/重连私发,只含你自己的牌;在手才有,否则 `null` |

## 8. 现在有 / 还没有(增量交付)

一句话:ws 主流程和 REST 面都齐了,缺的是 REST 的 TS 生成和前端自己的 client。

**已交付**

| 能力 | 报文 | 出处 |
|---|---|---|
| 进房 | `join_room` ↔ `user_joined` + 私发 `state_snapshot` | 0030 |
| 座位 | `sit_down` | — |
| 买入 | `buy_in` | — |
| 状态/起身 | `set_user_status` | — |
| 房间参数配置 | `set_small_blind`/`set_buy_in` ↔ `room_config_changed` | 0043/0044 |
| 开局 | `start_hand` | — |
| 动作 | `player_action` | — |
| 离开 | `leave_room` | — |
| 免盲投票 | `open_free_entry_vote`/`vote_free_entry` ↔ `free_entry_vote_updated`/`free_entry_vote_closed` | — |
| 房间聊天 | `room_chat` ↔ `chat_message` | — |
| 整桌快照 | `state_snapshot` | — |
| 私聊发路 | `direct_message` ↔ `dm_delivered`/`dm_undelivered` | 0038 |
| 已读回执 | `dm_mark_read` ↔ `dm_read` | 0039 |
| 登录补收 | — | 0040 |
| 聊天表情 `[code]` 目录 | — | 0035,见 §4 注 |
| 全套 REST 面 | — | 见 §10 |

除表内点名的能力外,§4 列出的其余所有 `ServerMessage`(`hand_started`/`hole_cards`/`player_acted`/`user_left` 等)也都已交付。

> 登录补收对前端透明:(重)连上后,后端会主动私发两类消息,你不用发任何请求——你离线期间的未读 `dm_delivered`(顺序旧→新),以及别人读你消息的 `dm_read`。它们与在线实时消息同形,按 `msg_id` 去重即可。

**还没有**

- REST DTO 的 TS 生成。本机无 node,`openapi-typescript` 待解(见 [wire.md](wire.md))。后果:§10 各端点的形状暂以后端 `.py` 为准;手写调用时别把它们塞进 `wire.gen.ts`,那是 ws codegen 的只读产物。
- 前端 WS client / 组件消费本身。用 `wire.gen.ts` + 本指南实现,替换 mock 的 `poker.ts`。

## 9. 怎么连

一句话:两个 WS 端点并存,dev 用明文,正式用加密;载荷 JSON 一模一样。前端切到加密后,明文端点退役。

**明文 dev 端点已退役(0086)**:`/dev/ws?nick=` 曾是无加密的开发捷径,前端加解密落地后按 [connection.md](connection.md) 的既定条件关掉了——它无鉴权,谁连上都能冒充那个 nick。现在只有加密端点一条路。

- 连上即用上面的报文收发,用文本帧,直接 `JSON.stringify` / `JSON.parse`。搭 UI / 联调最省事。

**加密**:`ws://<host>/ws?sid=<session_id>`(见 [0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md))。

- 先 `POST /user/login` 拿 `{session_id, session_token}`(精确形状见 §10 登录),再用 `session_id` 连 `?sid=`。
- 此后每帧是二进制信封 `iv‖ct‖mac`,用会话密钥做 SM4 + HMAC-SM3 + seq。这层帧的加解密要你自己实现,`send/receive` 用 binary。
- 载荷仍是同样的明文 `ServerMessage`/`ClientMessage` JSON。加密只包在外层,`switch(type)` 分发逻辑一字不改。

起服务:`cd service && .venv/bin/uvicorn app.shell.lifespan:app`。

**加密层要点**

1. 派生密钥:`enc_key=KDF_sm3(session_token+\x01,16)`、`mac_key=KDF_sm3(session_token+\x02,32)`。
2. 出站:`ct=SM4_CBC(enc_key, iv, seq(8B,BE)‖json)`,`mac=HMAC_SM3(mac_key, iv‖ct)`,帧 = `iv‖ct‖mac`。
3. 入站顺序:先验 MAC,再解密,再验 seq。
4. seq 逐会话严格递增。跨重连要续用同一 seq,只有重新 `/user/login` 换会话才从头;否则重连首帧会被服务端当重放拒。
5. `session_id` 只在握手 URL 给一次,逐帧不带。

**握手后**

- 连上直接进「大厅」,后端 `Connect` 对纯大厅是 no-op;主动发 `join_room{room}` 载入房间(见 0030)。
- 可以先对着 `wire.gen.ts` 用 mock 数据搭消息分发、UI 组件、桌面状态机,再切真 socket。

## 10. REST 面(大厅 / 排行 / 历史 / 登录 / 资料)

一句话:**除登录外一律走加密信封 POST**;登录是唯一暴露在外的入口(0094)。

> REST DTO 暂无 TS 生成(见 §8)。下列形状以后端 [`app/rest/*.py`](../app/rest/) 为准,本节只给「怎么调」。
>
> 设计细节见 [rest.md](rest.md) / [auth.md](auth.md)。

**读接口**:同样走信封(`POST` + `{sid, frame}`),要已登录。数据来自 DB / committed world,可滞后一拍;只作展示,别做实时判定。响应统一包一层对象,不是裸数组。

| 端点 | 内层请求 → 响应 | 说明 |
|---|---|---|
| `POST /lobby/rooms` | `{}` → `{rooms: [{id, small_blind, big_blind, buy_in, max_seats, seated, watching, status}]}` | 大厅房间列表;v1 靠轮询,几秒一次足够。`seated` 含断线保座的人 |
| `POST /leaderboard` | `{limit?}` → `{entries: [{rank, nickname, points}]}` | 排行榜;排的是结算后的全局积分,买进牌桌的筹码不计,要离桌结算后才回到全局积分 |
| `POST /hands` | `{room?, user?, before?, limit?}` → `{hands: [{id, dedupe_key, start_time, end_time, final_pot, participants:[{nickname, initial_points, final_points, net}]}]}` | 手牌历史,新→旧。游标分页,`before` 传上一页末条的 `id`;`user` 按昵称过滤参与过的手;记录只有结果,没有底牌 |

> 参数越界或类型不对回 **400**(信封已验过 ⇒ 是客户端 bug,不是鉴权问题),不会被默默截断成合法值;信封本身不过一律 **401**。

**登录**:引导信道。明文 HTTP,body 用 `K_user` 加密。`K_user` 是管理员带外发你、要你手输的 16 字节密钥。

```
POST /user/login   { name, iv, blob }
  blob = hex( SM4_CBC(K_user, iv, JSON{ password, client_nonce, ts }) )
    ts           = 当前 epoch 秒(须落在服务端新鲜窗内;NTP 大漂移会 401)
    client_nonce = 每次新随机串(同包重投会被判重放 → 401)
  响应 { iv, blob = hex( SM4_CBC(K_user, iv2, JSON{ session_id, session_token, exp, rotate }) ) }
    session_token 只留内存、绝不落 storage/URL;exp 到点前用 K_user 静默重登换会话(无感轮换)
    rotate=true   = 你用的是旧一代 K_user(轮换宽限期内):提示用户尽快换用管理员新发的密钥
```

- 任何失败一律 `401`,包括账号、密码、blob、重放,不泄具体原因。重试要用新 nonce + 新 ts 重封。

**需身份的端点**:会话信封(0062)。拿到会话后,一切带身份的 REST 都长这样。

- 请求 `POST { sid, frame }`:`sid` = 登录拿的 `session_id`;`frame` = hex(`iv‖ct‖mac`),内层明文 = `seq(8B,BE) ‖ 参数 JSON`,无参数就是 `{}`。
- 密钥与 ws 分域:REST 用 `KDF_sm3(session_token+\x03,16)` / `(+\x04,32)` 派生 enc/mac。别拿 §9 的 ws 密钥封 REST——分域是为了防跨信道重放,封错 MAC 必败 401。
- seq 规则:
  - 同会话请求 seq 严格递增、不复用。并发时各占一个号,乱序到达服务端滑动窗能容。
  - 响应内层回显你的请求 seq,校验相等即防旧响应答新请求。
  - 重试 = 新 seq 重新封帧,原帧重投必被判重 401;所以别把 401 当「必须重登」的唯一信号,先换 seq 重试一次。
- 错误两段式:信封任何一步不过 → 统一 `401`,fail-closed;信封验过之后的失败,如 DB 错 → 明文 `500`,不用重登。

| 端点 | 内层请求 | 内层响应 / 失败 |
|---|---|---|
| `POST /user/me` | `{}` | `{name, nickname, points}`,其中 `points` 是 DB 滞后值(精确余额看 ws 的 `state_snapshot` 或买入广播) |
| `POST /user/password` | `{old_password, new_password}` | `{status:"ok"}`;旧密码错/未启用 → `403`,缺参/空 → `400` |
| `POST /user/nickname` | `{new_nickname}` | `{status:"ok", nickname}`;仅大厅可改(在房 → `403`),撞名 → `409`,同名/空/首尾空白/超长 → `400` |
