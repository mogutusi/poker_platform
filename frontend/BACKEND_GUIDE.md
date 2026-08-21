# 后端对接手册(写给前端)

> 这份文档帮你**理解服务器是怎么工作的**,以及**每个接口该怎么用**。它不规定你的前端代码怎么组织——状态管理、组件划分都是你的事;但协议形状、加密算法、语义约定是双方的契约,照这里写。
>
> 更细的资料:ws 消息逐条目录见 [wire-protocol-guide.md](../service/docs/wire-protocol-guide.md);类型永远以生成的 [`src/types/wire.gen.ts`](src/types/wire.gen.ts) 为准。

---

## 1. 服务器的心智模型:一问一答的状态机

后端是**单进程、内存权威**的状态机。理解下面五句话,大部分接口行为都能推出来:

1. **你发出去的每条消息都是一个「命令」**,进入服务器唯一的串行队列,一条一条处理。没有并发竞态——你不用担心"两个请求打架"。
2. **牌局的一切结果由服务器算**。前端只负责:把用户意图发出去、把服务器推来的消息渲染出来。**不要在前端实现任何牌局规则**(谁赢、能不能加注、边池怎么分)——本地算的和服务器算的对不上时,以服务器为准,而且服务器不会告诉你它和你想的不一样。
3. **成功 = 广播,失败 = 私发 error**。命令合法,所有相关人(包括你)会收到广播消息;命令非法,只有你会收到一条 `error`,世界没有任何变化。所以前端不需要"乐观更新再回滚"——等消息驱动渲染即可,内网延迟毫秒级。
4. **断线不丢状态(在座者),重连拿快照**。**坐在桌上**的玩家掉线后座位和筹码保留一段时间(默认 90 秒,从断线时刻起算),窗口内重连成功服务器会私发一条 `state_snapshot`,一次性给你整桌当前状态。**前端不需要事件回放/补历史**——任何时候拿到 `state_snapshot` 就把桌面整个重建一遍。两个例外要知道:**观战者掉线会被立即移出房间**(没有座位筹码,重进零成本)——重连后在大厅,想回去就再发一次 `join_room`;**在大厅重连不会收到任何消息**(没有状态可对齐),别把"等到快照"当成重连完成的信号。
5. **心跳不用你操心**。协议里没有任何应用层 ping 消息,也**不需要**你实现:死连接由 WebSocket 协议层的自动 ping/pong 检测(服务器每 20 秒发协议 ping,浏览器的 `WebSocket` 自动回 pong,对 JS 完全透明)。静默观战几小时也不会被误判掉线。

另外两条有用的保证:

- **单连接严格保序**:同一条 ws 上,消息到达顺序 = 服务器发送顺序,放心按序处理。
- **同一账号只有一条有效连接**:同一昵称开第二条连接,旧连接被静默顶掉(新连接收快照对齐)。这是特性(换设备/刷新页面),不是 bug。

## 2. 用户在服务器上的「位置」

```
登录(REST) ──▶ 连上 ws(绑定昵称)──▶ 大厅 ──join_room──▶ 房间 ──leave_room──▶ 回大厅
```

- **大厅**:连接活着,但不在任何房间。能干的事:走 REST 看房间列表/排行/历史、收发私信、改昵称/密码。
- **房间**:发 `join_room{room}` 进入(先观战)。**房间不存在会自动创建**——房名就是 ID,谁都能建,建的人没有特权,最后一个人离开房间就消失。
- 一个人同时只能在一个房间;想换房先 `leave_room`。
- 观战 → `sit_down` 入座 → `buy_in` 买入筹码 → `set_user_status{"ready_to_play"}` → 有人 `start_hand` 就开打。

## 3. 两条通道:ws 管实时,REST 管查询和账号

| 通道 | 干什么 | 数据新鲜度 |
|---|---|---|
| **WebSocket** | 牌局全部交互、房聊、私信、状态快照 | 实时,内存权威 |
| **REST** | 登录、房间列表、排行榜、手牌历史、个人资料/改密/改昵称 | 落库值,可能滞后几百毫秒 |

一个要点:REST 里看到的积分是**结算落库后的值**,略滞后;桌上的精确筹码以 ws 消息(`state_snapshot`、买入/结算广播)为准。大厅展示用 REST 值完全够。

## 4. 国密加密层(必读,绕不开的部分)

服务器不走 TLS,而是在**应用层**用国密三件套自建加密信道。前端要自己实现这一层(仓库里目前只有 Python 参考实现)。好消息:**牌局 UI 的全部开发可以先跳过它**——明文 dev 通道(§4.5)覆盖 ws 上的一切;但注意明文旁路**只有 ws 这一条**:登录、以及 `/user/me`/改密/改昵称这几个 REST 端点没有明文变体,要联调它们就得先有加密实现。

### 4.1 你需要的三个算法

| 算法 | 用途 | 实现要点 |
|---|---|---|
| **SM4-CBC** | 对称加密 | 128-bit 密钥;**PKCS#7 填充**(数据恰为 16 字节整数倍时,额外补一整块 16 个 `0x10`) |
| **SM3** | 哈希 | 标准 GB/T 32905,任何标准实现皆可 |
| **HMAC-SM3** | 消息认证 | **标准 HMAC 构造**:块长 64 字节,key 超长先 SM3 收缩,补零到 64,ipad=`0x36`/opad=`0x5C`,`SM3(opad‖SM3(ipad‖msg))` |

还有一个"密钥派生",其实非常简单:

```
KDF_sm3(input, n) = SM3(input) 的前 n 字节
```

Python 参考实现:算法本体在 [`lib/ttxsgm/ttxsgm/`](../lib/ttxsgm/ttxsgm/)(`sm3.py`/`sm4.py`),信道封装在 [`service/app/auth/channel.py`](../service/app/auth/channel.py)。

**已知答案测试向量:[`crypto-test-vectors.json`](crypto-test-vectors.json)(就在本目录)**——从后端同一套原语生成,覆盖 SM3/SM4(含填充边界 0/15/16/17/33 字节)/HMAC(含超长 key)/四个 KDF 域/完整 ws 帧与 REST 信封/登录 blob 双向。**先让你的 TS 实现把这份向量逐字节跑绿,再连真服务器**——向量过了,填充/字节序/域字节这类互通坑就已排除;剩下的端到端验证就是用 dev 口令真登录一次(§7)。该文件由后端生成、有测试守门,勿手改。

### 4.2 两把钥匙

| 密钥 | 从哪来 | 干什么 |
|---|---|---|
| **`K_user`** | 管理员私下发给用户(微信/当面),**用户在登录界面手动输入**;绝不写死在前端代码、绝不存 localStorage | 只在登录时用一次:加密密码、解密登录响应 |
| **`session_token`** | 登录响应里下发(32 字节) | 登录后一切流量的密钥源;**只放内存**,刷新页面就重新登录 |

`K_user` 每周轮换。如果登录响应里 `rotate: true`,说明用户输的是**上一代**密钥(还在几天的宽限期内)——登录照常成功,但你应该提示用户"密钥即将失效,请尽快改用管理员新发的密钥"。

### 4.3 登录握手(`POST /user/login`,明文 HTTP + 加密 body)

```
请求  { name, iv, blob }
  iv   = 16 字节随机数的 hex
  blob = hex( SM4_CBC(K_user, iv, JSON{ password, client_nonce, ts }) )
         ts           = 当前 epoch 秒(和服务器钟差超过约 2 分钟会被拒)
         client_nonce = 每次登录新生成的随机串(防重放,重复即 401)

响应  { iv, blob = hex( SM4_CBC(K_user, iv2, JSON{ session_id, session_token, exp, rotate }) ) }
  session_id    = 公开句柄(连 ws、调 REST 时带,相当于"我是哪个会话")
  session_token = 秘密(hex 编码的 32 字节),派生所有后续密钥
  exp           = 会话过期时刻(epoch 秒);到点前用缓存的 K_user 静默重新登录即可无感续期
```

**`exp` 是强制的**:过期会话不只挡新连接——已连着的 ws 也会在下一帧收发时被服务器关闭(close code **4401**)。所以务必在 `exp` 前留余量做静默重登(新连接会自动顶替旧连接,用户无感);看到 4401 就是"必须重新登录"。

登录失败**永远是 401,不告诉你具体原因**(账号不存在/密码错/包重放都长一样)——这是防探测的设计,前端文案统一"登录失败"就好。重试时必须换新的 `client_nonce` 和 `ts`。

### 4.4 登录之后:两套派生密钥,两种信封

用 `session_token`(hex 解回 32 字节)派生密钥,**ws 和 REST 各一套,不能混用**:

```
ws:    enc_key = KDF_sm3(token‖0x01, 16)    mac_key = KDF_sm3(token‖0x02, 32)
REST:  enc_key = KDF_sm3(token‖0x03, 16)    mac_key = KDF_sm3(token‖0x04, 32)
```

**ws 帧**(连 `ws://<host>/ws?sid=<session_id>`,之后每帧是二进制):

```
帧 = iv(16B) ‖ ct ‖ mac(32B)
  ct  = SM4_CBC(enc_key, iv, seq(8字节大端) ‖ 明文JSON)
  mac = HMAC_SM3(mac_key, iv ‖ ct)
```

- **发**:seq 从 1 开始每帧 +1;IV 每帧新随机。
- **收**:**先验 mac,验过才解密**,再检查 seq 比上一帧大。
- **seq 按会话计,不按连接计**:断线重连(同一个 `session_id`)后,seq **接着之前的数**继续,不要归零——归零会被服务器当重放拒掉。只有重新登录(新 session)才重置。
- 解出来的明文就是普通的 `ServerMessage` JSON——加密只是外壳,你的消息分发逻辑和明文通道完全一样。

**REST 信封**(需要身份的端点,如 `/user/me`):

```
请求  POST { sid, frame }     frame = hex( iv ‖ ct ‖ mac ),内层 = seq(8B大端) ‖ 参数JSON(无参就是 {})
响应  { frame }                内层 = 你的请求 seq(原样回显)‖ 响应JSON
```

- REST 的 seq 独立计数,同样只增不复用;**响应里回显的 seq 必须等于你发的**(校验一下,防旧响应)。
- **重试 = 用新 seq 重新封包**。同一个包原样重发会被判重放(401)。所以收到 401 别急着让用户重新登录——先换 seq 重试一次,还是 401 才是会话真过期了。
- 信封本身有问题 → 401;信封没问题但服务器内部错 → 500(不用重新登录)。

### 4.5 开发期捷径:明文通道

后端同时开着一个**不加密的 dev ws 端点**:`ws://<host>/dev/ws?nick=alice`(昵称直接写在 URL 上,文本帧,`JSON.stringify`/`JSON.parse` 直接用)。**先用它把整个牌局 UI 和消息流跑通,最后再实现加密层**,两块工作可以完全分离。这个端点只在开发环境存在,上线会移除。范围要清楚:它只旁路了 **ws**;登录和需身份的 REST(§6 第二张表)没有明文版,那几个页面要等加密层就绪才能真联调。

## 5. ws 协议怎么用

类型全部在 [`src/types/wire.gen.ts`](src/types/wire.gen.ts)——它是后端自动生成的,**只 import 不手改**(后端有测试保证它和服务器实现永远一致)。五条铁律:

1. 每条消息带 `type` 字段,是可辨识联合:`switch (msg.type)` 分发,TS 自动收窄类型。
2. 遇到不认识的 `type` **静默忽略**(后端会加新消息,这是向后兼容的关键)。
3. 字段全是 `snake_case`,别转驼峰(转了和生成类型对不上)。
4. **你发的消息里没有"我是谁"**——身份由连接决定,报文只有动作参数。
5. 枚举值用生成的字面量(`"ready_to_play"`、`"fold"`),别写裸字符串。

一手牌的消息流(简版,完整时序见 [协议指南 §5](../service/docs/wire-protocol-guide.md)):

```
start_hand 后你会收到:
  hand_started(谁在玩、庄位、盲注)→ hole_cards(私发,你的两张牌)
  → hand_status_changed(街推进,翻公共牌)⇄ player_acted(每次有人动)
  → hand_show_down(摊牌,揭示未弃牌者的牌)→ hand_ended(结算)
```

**几个最容易理解错的语义**:

- `acting_position` 是 `players[]` 数组的**下标**,不是座位号。"轮到谁" = `players[acting_position]`,他坐哪 = `.seat_position`。
- `player_action` 的 `bet_amount` 是**本街目标总额,不是增量**:跟注 = 把它设成当前 `last_bet`;加注 = 设成更大的数;all-in = 设成自己的全部。
- 你**永远收不到别人的底牌**(广播消息里结构上就没有这个字段),只有三处例外:私发给你的 `hole_cards`、摊牌的 `hand_show_down.reveals`、快照里的 `your_hole_cards`(只有你自己的)。渲染时对手的牌一律牌背,摊牌才翻。
- 房聊历史(`fetch_room_chat`)只有最近 N 条且**随房间销毁而消失**——人走光房间就没了,之后建的同名房是全新历史。
- 聊天文本里的表情是 `[code]` 形式的文本 token(如 `[thumbs_up]`),用 `wire.gen.ts` 里的 `EMOJI_CATALOG` 渲染([`src/utils/emoji.ts`](src/utils/emoji.ts) 已经有现成的 tokenizer);不认识的 code 原样显示。
- `error` 消息只回给你,带机器码 `code`(如 `NOT_YOUR_TURN`)和调试用的 `detail`。**按 `code` 映射你自己的中文文案,`detail` 别直接给玩家看**。
- **你可能因为「读得太慢」被服务器主动断开。** 每条连接的出站队列是有界的,你迟迟不收(页面卡住、标签页被冻结、网络单向拥塞)就会把它灌满;满了服务器就丢弃这条连接——**关掉 ws + 按断线处理**(在座的话保留座位与筹码,直到占座窗口到期)。你看到的就是一次普通掉线,照常退避重连即可:重连后 `state_snapshot` 会把落下的全部补回来,服务器不做增量补发。别当成协议错误去查密钥或 seq。(0083 之前服务器只是停止给你发消息、并不关连接,那才是真正难查的现象。)

## 6. REST 接口一览

**公开(明文 GET,无需登录)**:

| 端点 | 返回 | 用法 |
|---|---|---|
| `GET /lobby/rooms` | 房间列表(配置 + 在座/观战人数) | 大厅页轮询,几秒一次 |
| `GET /leaderboard?limit=N` | 排行(名次/昵称/积分) | 积分是结算值,桌上筹码不计 |
| `GET /hands?room=&user=&limit=&before=` | 手牌历史(新→旧) | 游标分页:`before` 传上一页最后一条的 `id`;记录只有输赢,没有底牌 |

**需要身份(走 §4.4 的 REST 信封)**:

| 端点 | 内层请求 → 响应 | 说明 |
|---|---|---|
| `POST /user/me` | `{}` → `{name, nickname, points}` | 个人资料 |
| `POST /user/password` | `{old_password, new_password}` → `{status}` | 改密码要验旧密码;403 = 旧密码错 |
| `POST /user/nickname` | `{new_nickname}` → `{status, nickname}` | **只能在大厅改**(在房间里 403);撞名 409 |

REST 的请求/响应类型**没有**自动生成(和 ws 不同),按上表和后端 [`service/app/rest/`](../service/app/rest/) 的字段注释手写,别把它们加进 `wire.gen.ts`。

## 7. 本地联调

从零把后端跑起来(含新机器 pull 后的完整步骤、连 PostgreSQL)见 [service/QUICKSTART.md](../service/QUICKSTART.md);已配好环境的话一条命令:

```bash
cd service
.venv/bin/uvicorn app.shell.lifespan:app        # http://127.0.0.1:8000
```

- 首次启动自动建 SQLite 库并种好 dev 用户(`alice`/`bob`/`carol`/`dave`/`eve`/`frank`,各 1000 积分)。
- **最快路径**:直接连明文端点 `ws://127.0.0.1:8000/dev/ws?nick=alice`,开两个页面用两个昵称就能对打。
- **要测登录/加密**:dev 用户可以真登录——账号 = 昵称,密码和 K_user 是共享的 dev 值(在 `service/app/poker.env.example` 里的 `DEV_PASSWORD`/`DEV_KUSER`;仅开发用)。
- REST 直接 `curl http://127.0.0.1:8000/lobby/rooms` 可验。

## 8. 常见坑清单(对接前扫一眼)

- ❏ ws 重连(同一会话)seq 忘了续 → 首帧就被当重放踢下线。
- ❏ SM4 填充没按 PKCS#7(尤其"整块也要再补一块"这条)→ 解密后 JSON 尾部多/少字节。
- ❏ 先解密后验 MAC → 顺序反了,必须先验 MAC。
- ❏ 拿 ws 密钥封 REST 信封 → MAC 验不过,统一 401 不告诉你原因;**反过来**(拿 REST 密钥封 ws 帧)→ 没有 401,ws 连接被**直接关闭**(close code 4400)。排障时认准现象:REST 出 401、ws 掉线,都先怀疑密钥域(0x01/0x02 vs 0x03/0x04)用混了。
- ❏ REST 401 直接弹"请重新登录" → 先换 seq 重试一次再说(可能只是重放误判)。
- ❏ 前端本地算牌局结果(能不能 check、谁赢)→ 只信服务器消息。
- ❏ 把 `acting_position` 当座位号用 → 它是 `players[]` 下标。
- ❏ `session_token` / `K_user` 存 localStorage → 只准放内存。
- ❏ 手改 `wire.gen.ts` → 下次生成就被覆盖,想改类型去改后端 `.py`。

## 9. 想深入时读什么

| 文档 | 内容 |
|---|---|
| [wire-protocol-guide.md](../service/docs/wire-protocol-guide.md) | ws 每条消息的字段/语义逐条目录、完整时序、REST 契约细节(本手册的展开版) |
| [auth.md](../service/docs/auth.md) | 加密信道的完整设计:威胁模型、密钥层级、轮换、防重放 |
| [architecture.md](../service/docs/architecture.md) | 服务器内部架构(core/shell、单写者、内存权威) |
| [rules.md](../service/docs/rules.md) | 德州扑克规则的精确定义(盲注/下注轮/边池)——UI 提示文案可参考,逻辑别搬 |
