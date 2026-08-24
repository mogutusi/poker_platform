# 状态与场景映射

一句话:`StateSnapshot` 是整份真相,事件是它的增量;界面上的每个操作都对应一条 `ClientMessage`。

## 状态从哪来

进房或重连时,服务器私发一份 `StateSnapshot`,里面是**为你这个收件人投影过的**整桌状态:座位、筹码、当前手牌进度、公共牌,以及**你自己的底牌**(别人的底牌不在里面,是结构性缺位,不是置空)。

之后靠事件增量维护:

| 事件 | 改什么 |
|---|---|
| `HandStarted` | 新一手开始:清上一手的展示、设置盲注位与行动者;底池取 `pot`(**盲注已下,不是 0**)|
| `HoleCards` | 私发,只给自己:填自己的两张底牌 |
| `HandStatusChanged` | 街道推进,带新发的公共牌,以及**本街的 `last_bet` 与 `players[]`**——照抄,别自己推「换街了所以清零」(开局那条 `pre_flop` 上盲注已经在桌上,推错会让整轮 preflop 跟注发成 0,见 [changes/0087](../../service/docs/refactor/changes/0087-reconnect-and-displacement-in-browser.md))|
| `PlayerActed` | 某人行动:更新其筹码/状态、底池、下一个行动者 |
| `HandShowDown` | 摊牌:这是**唯一**会出现别人底牌的地方 |
| `HandEnded` | 结算:各家筹码回座 |
| `UserJoined` / `UserLeft` / `UserStatusChanged` | 房间成员与状态 |
| `PlayerBoughtIn` | 某人买入 |
| `RoomConfigChanged` | 盲注/买入额被改 |
| `ChatMessage` / `RoomChatHistory` | 房聊 |
| `FreeEntryVoteUpdated` / `FreeEntryVoteClosed` | 免盲投票面板 |

失去同步了怎么办:**不要自己修**。断开重连,服务器会补一份新快照。

## 事件顺序是有契约的

后端保证单连接严格保序(见 [service/docs/architecture.md](../../service/docs/architecture.md)),所以可以放心按到达顺序处理,不需要自己排序或缓冲。

## 场景 → 命令映射

合并进来的三个页面已经把界面设计好了([changes/0076](../../service/docs/refactor/changes/0076-frontend-merge.md)),接线就是把每个交互接到对应命令上。

### 登录页 `/`

| 界面 | 实际做什么 |
|---|---|
| 账号 + 密码 + `K_user` | `POST /user/login`(见 [transport.md](transport.md)) |
| 登录成功 | 存 `session_token` 到内存,连 ws,跳 `/lobby` |

`K_user` 输入框是**必须新增的**:原设计只有账号密码,但没有 `K_user` 就无法构造登录 blob。已缓存过就不必每次输,提供「换一把钥匙」的入口即可。

### 大厅 `/lobby`

| 界面 | 实际做什么 |
|---|---|
| 房间列表 | `GET /lobby/rooms` → `RoomMeta[]`(id、盲注、买入、座位数、在座/观战人数、状态) |
| 排行榜 | `GET /leaderboard` → `LeaderboardEntry[]`(rank、nickname、points) |
| 进入房间 | ws 发 `join_room{room}`;房不存在就动态建房 |

**这里有个设计取舍要说清楚。** 原 UI 的大厅直接画了一张 9 座的桌子,点空座就进牌桌。但后端是多房间模型,而 `GET /lobby/rooms` 只给房间的**汇总信息**(人数、盲注),给不出每个座位上是谁——逐座位的详情要 `join_room` 之后由 `StateSnapshot` 带来。

所以调整为:**大厅展示房间列表(沿用现有的卡片与排行榜视觉),选座挪到牌桌页**。进房后你是观战状态,在牌桌上点空座才是 `sit_down`。这既符合后端的命令序,也符合真实牌桌的体验(先入座、再买入、再准备)。原大厅那套座位视觉不浪费,直接复用到牌桌页。

### 牌桌 `/game`

进房后的完整命令序:

```
join_room{room}          → 你在房间里,状态 WATCHING,收到 StateSnapshot
sit_down{seat, wait_for_big_blind}  → 观战 → 就座
buy_in{seat, amount}     → 全局积分转成桌上筹码
set_user_status{ready}   → 准备好了
start_hand               → 开新一手(任何在房成员都能发)
player_action{action, bet_amount?}  → fold / check / bet
leave_room               → 退分离桌回大厅
```

| 界面 | 命令 |
|---|---|
| 点空座位 | `sit_down`;界面要让用户选「付盲即玩」还是「等大盲免费」(见 [rules.md](../../service/docs/rules.md) ①) |
| 买入 | `buy_in` |
| 准备 / 取消准备 | `set_user_status` |
| 开始游戏 | `start_hand` |
| 弃牌 / 过牌 / 跟注 / 加注 / all-in | 都是 `player_action`;**跟注、加注、all-in 在协议上都是 `bet`**,区别只在 `bet_amount`(它是本街目标总额,不是增量) |
| 离开 | `leave_room` |
| 聊天 | `room_chat` |

要拆掉的 mock:`createDeck` 本地发牌、本地推进 preflop→flop→…、本地算底池,全部删掉,改由事件驱动。

### 行动按钮怎么算

`bet_amount` 是**本街目标总额**,不是这次要加多少。所以:

- 跟注 = `bet_amount = last_bet`
- 加注到 X = `bet_amount = X`,且必须满足 `X ≥ last_bet + max(last_raise_size, BB)`
- all-in = `bet_amount = 自己的 points + 本街已下注`

前端可以按这个规则**灰掉**不合法的按钮以改善体验,但**不能据此认定合法**——最终判定在服务器,被拒了就按返回的 `ErrorCode` 提示。

## 错误怎么显示

服务器只回机器可读的 `ErrorCode`(见 `wire.gen.ts` 里的枚举),**文案由前端映射**。这是有意的设计:后端不做多语言。

常见的几个:`NOT_YOUR_TURN` 不是你的回合、`ILLEGAL_ACTION` 这个动作不合法、`INSUFFICIENT_POINTS` 积分不够、`NOT_ENOUGH_PLAYERS` 人不够开局、`RATE_LIMITED` 发太快了。

## UI 局部状态归谁

不是所有状态都来自服务器。分界线:

- **服务器的**:座位、筹码、牌、谁该行动、房间配置、聊天记录。
- **前端自己的**:加注滑块当前值、面板开合、动画进度、输入框内容、乐观的「按钮已点击、等回执」置灰。

前端自己的那部分不要往服务器同步,服务器的那部分不要在本地擅自改。
