# 前端架构

一句话:前端只做「渲染服务器给的状态 + 把用户操作发成命令」,自己不裁定任何牌局规则。

适用范围与后端一致:单进程、内网、在线玩家 ≤ 20、房间极少(见 [service/docs/architecture.md](../../service/docs/architecture.md))。

## 最重要的一条:服务器是唯一真相

牌局的一切判定都在后端 `reduce` 里:谁该行动、能不能 check、底池怎么分、谁赢。前端**不复算、不预测、不本地推进**。

- 收到 `StateSnapshot` 就整份替换本地状态,不做合并。
- 收到增量事件(`PlayerActed`/`HandStatusChanged`/…)就按事件改对应字段。
- 用户点「跟注」,前端发一条 `player_action` 就结束了;界面等服务器回事件再变,不抢先改。

这条是从后端的「单写者 + 内存权威」延伸过来的。前端自己算一份牌局,只会和服务器分叉。

> 合并进来的 UI(0076)里有一套本地 mock 牌局(`createDeck` 发牌、本地推进街道)。那是没有后端时的占位,接线时**整套拆掉**,不保留任何「双份真相」。

## 分层

```
app/            页面与路由(Next App Router):登录 / 大厅 / 牌桌
  ↑ 只读 store,只调 actions
store/          客户端状态:会话、房间快照、UI 局部态
  ↑ 只被 transport 写
transport/      与后端通话:登录握手、ws 信道、REST 信封
  ↑ 只用 crypto
crypto/         国密原语:SM3 / SM4-CBC / HMAC-SM3 / KDF(纯计算,无 IO)
types/          wire.gen.ts(后端 codegen 产物,只读)+ 本地 UI 类型
```

依赖方向单向向下,不允许回指:

- `crypto/` 纯函数,不认识 fetch、WebSocket、React。
- `transport/` 不认识 React,不 import 任何组件;它只暴露「发命令」和「订阅事件」。
- 组件不直接碰 WebSocket,也不自己拼加密帧,一律经 `transport/`。

理由和后端把 IO 外移是同一个:把「会失败、会重连、有时序」的部分关在一层里,其余部分才好推理。

## 数据流

```
用户点击 ──▶ store action ──▶ transport.send(ClientMessage)
                                     │ SM4+HMAC 封帧
                                     ▼
                                  WebSocket
                                     │
             store ◀── 事件分发 ◀── transport(验 MAC → 解密 → 验 seq → JSON)
               │
               ▼
             组件重渲染
```

REST 走另一条路,只用于事后查询和账号操作:排行榜、手牌历史、房间列表、登录、改密码/昵称。**牌局操作一律走 ws,不走 REST。**

## 协议类型只有一份

`src/types/wire.gen.ts` 是后端 Pydantic 经 codegen 生成的,**只读,禁止手改**。前端要新字段,改后端 `.py` 再重新生成(见 [service/docs/wire.md](../../service/docs/wire.md))。

`src/types/poker.ts` 是 UI 自己的展示类型(牌面渲染等),**不是协议类型**。两者不许混:凡是和服务器交换的数据,类型来自 `wire.gen.ts`。

> 这条曾经被破坏过:`poker.ts` 里手写过一份协议形状,两种漂移各出一次——`Player.chips` 是**字段名**对不上(后端叫 `points`),`GameState.phase` 是**枚举取值**对不上(后端是 `pre_flop`/`flop`/…,见 `HandStatus`)。**已于 [0099](../../service/docs/refactor/changes/0099-retire-the-mockup-types.md) 清理**——数引用发现那几个接口(以及唯一用到它们的 `PlayerSeat` 组件)零消费者,协议面早就走 `wire.gen.ts` 了,于是连同死代码一并删除。`poker.ts` 现在只剩 `Card`。

## 身份不进报文

发给服务器的报文里**不带自己是谁**。身份由连接绑定(`?sid=` 查会话拿 nick),服务器不信客户端自报。所以 `player_action` 只带动作和金额,不带 nickname。

## 前端不变量(任何改动必须守住)

1. **不复算规则**:合法动作、底池、胜负一律以服务器事件为准;前端只做展示层面的推导(例如把 `acting_position` 换算成高亮哪个座位)。
2. **快照即真相**:收到 `StateSnapshot` 整份替换,不与本地状态合并。
3. **只经 transport 通信**:组件里不出现 `new WebSocket`、不出现手拼加密帧。
4. **协议类型只用 `wire.gen.ts`**,不手写第二份、不改生成产物。
5. **秘密不落可见处**:`session_token`、`K_user` 不进 URL、不进日志、不渲染到 DOM(见 [transport.md](transport.md))。
6. **seq 单调**:同一会话的 ws 序号只增不减,跨重连继续累加,只有重新登录才归零(见 [transport.md](transport.md))。

## 与后端文档的对应

| 前端 | 后端 |
|---|---|
| [crypto.md](crypto.md) | [auth.md](../../service/docs/auth.md) §加密信道 |
| [transport.md](transport.md) | [auth.md](../../service/docs/auth.md) §登录握手、[wire-protocol-guide.md](../../service/docs/wire-protocol-guide.md) |
| [state.md](state.md) | [core.md](../../service/docs/core.md)、[connection.md](../../service/docs/connection.md) |
| [dev.md](dev.md) | [service/docs/dev.md](../../service/docs/dev.md) |
