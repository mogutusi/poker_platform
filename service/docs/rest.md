# REST 查询面与用户资料(rest)

## 定位

排行榜、手牌历史、用户资料这类**事后/聚合查询 + 账号管理**走 REST,不进 WS / reduce / world。它们是 [connection.md](connection.md) 里 **C 落点**(REST 读 DB)的集合。

> 前置:[db.md](db.md)(读路径走请求级 `DBsession`、DB 比内存滞后)、[auth.md](auth.md)(REST 走现有 JWT)、[storage.md](storage.md)(内存权威 + delayDB)。

## 共同原则(三个模块都守)

1. **读 DB,不读 `world`**:REST 读的是 delayDB 落库后的值,**比内存滞后**——这对排行榜/历史/资料的展示完全够用;**实时判定一律以内存为准**(下注、余额够不够在 reduce 里判,不在 REST)。
2. **请求级 `DBsession`**:每请求一个 session,与 PersistWriter 的写 session 互不复用(见 [db.md](db.md));读路径无行锁。
3. **鉴权走 JWT**:REST 端点用现有 JWT Bearer(`sub=name`),与 ws 的 `K_user`/`session_token` 两套各管各(见 [auth.md](auth.md) 的「token 层级」)。本规模 JWT 仍明文裸奔的问题见 [auth.md](auth.md) 待办。
4. **wire/DTO 同源**:REST 响应模型也是 Pydantic,经 OpenAPI → TS 生成,前端不手写(见 [wire.md](wire.md))。

## 排行榜 leaderboard

```
GET /leaderboard?limit=N  →  [{ rank, nickname, points }]
```

- 读 DB `users` 按 `points` 降序取前 N。
- **坑 · 排的是"结算后的全局积分",不是身家**:玩家买进牌桌的筹码在 `Seat.points`(内存、不落库),**不在 DB**。所以一个把积分全买进牌桌的人,排行榜上只显示他**桌下剩余的全局积分**。这是因为「room 状态不落库」(见 [storage.md](storage.md))。
  - **决策(可改)**:排行榜定义为**银行余额(settled points)**——离桌(`LeaveRoom`)结算把筹码还回全局后才完整体现。这个定义清晰、且只读 DB 就够;若要"含桌上筹码的总身家",得读 `world`(跨切),列为 future。
- DB 滞后:刚买入/离桌的变更可能还没 flush,排行榜短暂偏旧——可接受。

## 手牌历史 hand history

```
GET /hands?room=&user=&limit=&before=  →  [HandRecord]   (分页)
```

- 读 DB 的手牌记录(由 delayDB **事件写**追加,见 [db.md](db.md));复用现有 [handrecord](../app/handrecord/) 模块,**对齐新的 `HandRecordWrite` 结构**(`start_time`/`end_time`/`final_pot` + participants 的 `initial_points`/`final_points`)。
- **隐私**:记录只存**结果**,`hole_cards`/`deck` **从不落库**(见 [core.md](core.md) 不变量 3 / [log.md](log.md))——所以历史查询能看输赢、看不到底牌。摊牌时的底牌只在当时的 `HandShowDown` 事件里露过,不进历史。
- 分页用 `before`(游标,按 `hand.seq` 或 `end_time`),不用 OFFSET 翻大表。

## 用户资料 profile

```
GET   /user/me            →  { name, nickname, points, ... }     # points 取 DB(滞后)
PATCH /user/nickname      →  改昵称(仅大厅)
PATCH /user/password      →  改密码(SM3+盐+迭代,见 auth.md)
```

- **`GET /user/me` 的 `points`** 取 DB(滞后);**精确余额在 ws**(进房后 `StateSnapshot` / 买入广播给的是内存权威值)。大厅展示用 DB 近似值即可。
- **改昵称:仅当用户不在任何房间**(你的决策)。`nickname` 是 `world` 的键(座位/`contributed`/ConnectionManager 全按它),在用时改会让键错乱;大厅用户不在 `world.users`,改它安全。
  - **判定"是否在房" + 连接重挂的完整机制见 [presence.md](presence.md)**:`current_room(nick) is None`(在大厅)才允许;改名后若有 live 连接,把 ConnectionManager 从 `old_nick` 重挂到 `new_nick`(`rename`),并更新 DB/会话表的 nickname。
  - 唯一性:`nickname` 全局唯一,改名走唯一约束校验。
- **改密码**:重算 `salt$rounds$digest`(见 [auth.md](auth.md)),与传输加密正交。

## 与架构契约(必须守住)

1. **REST 只读/写 DB,不碰 `world`**;实时判定一律以内存为准(REST 给的是滞后视图)。
2. **排行榜 = 结算后的全局积分**(桌上筹码不计,room 不落库);要含桌上筹码是 future(需读 world)。
3. **历史只存结果、绝无底牌**(`hole_cards`/`deck` 不落库)。
4. **改昵称仅限大厅**(不在 `world.users`),改后重挂连接 nick 键 + 更新会话表。
5. **REST 鉴权走 JWT**,与 ws 信道两套;请求级 `DBsession`、无行锁。

## 待定 / future

- **排行榜含桌上筹码的"总身家"**:需读 `world` 或把 `in_game_points` 也落库,本规模先不做。
- **统计维度**:胜率、手数、盈亏曲线——基于手牌记录聚合,加查询即可。
- **资料扩展**:头像、签名等;非游戏权威字段放 DB,不进 `UserState`(见 [user.md](user.md))。
- **REST 加密**:JWT 裸奔问题——日后用同一条 `K_user` 信道包一层或上 wss(见 [auth.md](auth.md) 待办)。
