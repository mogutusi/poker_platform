# 存储模型(内存权威 + 工作副本回滚 + delayDB)

## 一句话定位

**所有状态都活在内存(`world`),内存是权威;DB 只是滞后的持久层。** 一条命令进来,GameLoop 先**深复制一份工作副本**给 `reduce` 改,**成功才装回内存、失败整份丢弃**(这就是回滚);需要落库的改动以 `Persist` 事件交给 **delayDB** 异步追平 DB。

这一篇把存储的三件事讲全:**① 内存权威 + 载入一次 · ② 工作副本回滚 · ③ delayDB 写回**。delayDB 的写通道机制(写缓冲、双缓冲、重试、drain)细节在 [db.md](db.md);本文是上层模型与它的链接点。

> 前置:[architecture.md](architecture.md)(分层、不变量、GameLoop)。

## ① 内存权威 + 载入一次

- 凡需持久化的数据(当前:全局积分;手牌结束写手牌记录),**从 DB 读一次进内存,内存即权威**;此后改内存、由 delayDB 落库,**DB 不参与任何实时判定**。
- 读 DB 是 IO,**只能在 shell**(Receiver / lifespan),把读到的值随**命令**带进 core(如 `Connect(loaded=...)`),由 reduce 决定是否安装。**core 内绝不 `await` DB。**
- **绝不重载已在内存的实体**:内存比 DB 新(DB 滞后),重载会丢未落库的变更。是否安装的判定在 reduce(见 [user.md](user.md)),shell 不读 `world`(守不变量 2)。
- **启动初始化例外**:进程启动时内存为空、无任何已安装实体,故 **lifespan 在启动阶段直读 DB 初始化 `world`** 是允许的(典型:种子用户进 DB)——没有「内存更新值」会被覆盖,「绝不重载」针对的是**运行期**对已安装实体的重读。**用户积分一律在其 `JoinRoom` 时载入**(shell 读 DB → 随命令带进 core → reduce 决定安装)。dev shell 曾用启动期整体载入用户([0029](refactor/changes/0029-p4-db-backed-dev-shell.md)),**[0030](refactor/changes/0030-p4-per-join-wire-load.md) 起改为真 per-join 载入**(连接→大厅→`join_room`→Receiver 读 DB)。**动态房([0049](refactor/changes/0049-dynamic-rooms.md)):无静态预置,启动期 `world.rooms` 为空——房随 `JoinRoom` 到不存在的房而建、随空房而销毁**(房内一切含 `chat_history` 随之消亡——历史随房生灭是有意语义,0071)。
- 好处:买入这类高频操作是**纯内存转账**,不需要在 GameLoop 里 `await` DB(那会撞碎「reduce 不 await」);无并发写者 ⇒ **无行锁 / `with_for_update`**。

## ② 工作副本回滚(进业务就深复制一份)

**这是唯一的状态修改 + 回滚机制,所有命令一视同仁:**

1. GameLoop 处理命令前,对**工作集**深复制成**工作副本**——因为「每条命令只作用于一个房间」(不变量 8),工作集 = 目标房间 + 全局 `users`(默认整份拷,见下「大实体优化」)。
2. `reduce` 只改工作副本,校验与修改可随意穿插。
3. 返回 `(events, err)`:
   - **失败 / 抛异常** → **丢弃工作副本**,真正的 `world` 一字节没动。
   - **成功** → 把工作副本**装回** `world`(替换引用),再 dispatch events(含 `Persist`)。

`checkout` / `commit` 是 **`shell/world.py` 的模块级函数**,不是 `World` 的方法(`World` 是 core 纯 dataclass,挂方法会破坏分层,见 [models.md](models.md))。它们接收 `world` 作首参,返回 / 落定一个 `Work`(`room_name` / `room` / `users`)。**`Work` 的类型定义在 `core/domain.py`**(它是 reduce 的操作面;若放 shell 则 core/reduce import shell 会破「core 不 import shell」铁律),`checkout`/`commit` 只是其构造者/落定者(shell→core 合法)——0010 落 reduce 时上移,见 [changes/0010](refactor/changes/0010-p1-reduce-start-hand.md):

```python
# GameLoop 主循环(简化)
work = checkout(world, cmd)            # 解析目标房 + 深复制:目标房间 + users 表 → 工作副本
try:
    events, err = reduce(work, cmd)    # 只改副本
except Exception:
    events, err = [], Err(INTERNAL)    # 异常归一为失败
if err is not None:
    send_error(cmd, err)               # 丢弃 work,world 未动
else:
    commit(world, work)                # 装回(房间增/删/替换 + users 表替换)
    for ev in events: dispatch(ev)     # 只 put_nowait
```

### `checkout(world, cmd)`:目标房按命令类型解析(不是简单的 `cmd.room`)

模型 2 下命令大多**不带 room**,所以 `checkout` 接收整条 `cmd`、按类型解析目标房,再深拷「该房 + `users` 表」。**GameLoop 读 `world.users` 来解析房间是允许的**——它是唯一写者、单协程,读自己的已提交状态不破坏任何不变量(不变量 2 禁的是「其它协程写 world」「shell 读 world 做载入决策」,不禁 GameLoop 自己读):

| 命令 | 目标房解析 | 副本里有没有房 |
|---|---|---|
| `JoinRoom(room, …)` | 命令自带 `room` | 房可能不存在 → 给「无此房」的副本,reduce 负责新建 |
| 其余 wire 命令(`PlayerAction`/`SitDown`/`BuyIn`/`RoomChat`/…) | `world.users[cmd.origin].room` | 必有(用户已在房) |
| `Timeout(nick)` / `Cleanup(nick)` | `world.users[nick].room`(不在 `users` 则无房 → reduce 直接 no-op) | 视情况 |
| `Connect(nick)` / `Disconnect(nick)` | `nick` 在 `world.users` → 其 `room`(重连/在房断开);否则纯大厅 → **无房可拷** | 视情况 |

> 纯大厅的 `Connect`/`Disconnect` 没有目标房,`checkout` 只拷 `users` 表(或连 users 都不必动);reduce 对它们「core 无事」或只动 presence(在 shell)。

### `commit(world, work)`:房间的增 / 删 / 替换都在这里落定

`commit` 不只是「替换一个房间引用」,它把工作副本相对权威的差异整体落回 `world.rooms` 顶层 dict:

- **替换**:目标房存在且被改 → `world.rooms[room] = work.room`(替换引用,旧对象不再被原地改 ⇒ 跨命令隔离,不变量 7)。
- **新建**:reduce 在副本上建了新房(`JoinRoom` 到不存在的房)→ `world.rooms[room] = work.room` 插入。
- **销毁**:reduce 在副本上 `del` 了空房(最后一人离开)→ `del world.rooms[room]`。
- **users 表**:始终整份替换(小实体默认整份拷;大了改用 `uRead`/`uWrite`,见下)。

房间生命周期(建/销的精确时机)见 [core.md](core.md);销毁房时**不要再 `Broadcast` 到它**(见 [connection.md](connection.md) 的 dispatch 容错)。

为什么这样设计:

- **失败安全 = 没 commit**。业务校验失败和未预期异常走同一条路,都只是不 commit,不需要「先快照再恢复」的补偿动作,也**不需要用户专用的 `uRead`/`uWrite` 二分**——积分和房间状态都在同一份副本里,一起 commit、一起丢弃。
- **跨命令隔离天然成立**:commit 是「替换引用」,已提交的旧对象**不会再被原地改**(下一条命令拷的是新副本)。所以事件携带的对象在异步发送前不会被改写——这把不变量 7 从「每个事件都要深拷贝 payload」**减负**成「同一条 reduce 内,产出 event 后别再改它引用的对象」。
- **成本**:每条命令深拷一个小房间 + 小用户表,玩家 ≤ 20,开销极小。

## 大实体优化:整份深拷 vs `uRead` / `uWrite`

默认「整份深拷工作集」对**小实体**(如 `UserState` = 昵称 + 积分)足够好、且无脑安全。但如果某类实体**大到「每条命令都整份深拷」开销可观**、而**绝大多数命令只读它**,可以对**这一类实体**改用细粒度的读写分离,把它**移出**上面的整份深拷:

| 取法 | 给什么 | 用在 | 成本 |
|---|---|---|---|
| **`uRead(work, key)`** | 实体的**活引用**(只读) | 命令只读该实体 | 零拷贝 |
| **`uWrite(work, key)`** | 该实体的**深拷贝**(登记进工作副本的待写表) | 命令要改该实体 | 只拷这一个 |

收尾仍由 GameLoop 统一:成功 → 待写表里的拷贝装回权威 + 产出对应 `Persist`;失败 → 丢弃,权威没动。语义和「整份深拷」完全一致,只是**省掉了对只读实体的拷贝**。

**判据(按实体类型选一次,不混用):**

- 实体小、拷贝便宜(`UserState` 现状)→ **默认整份深拷**,简单、无 footgun。
- 实体大、且多数命令只读 → **`uRead`/`uWrite`**,把深拷省到「真要写」时。

**`uRead` 的唯一纪律(footgun)**:它给的是**活引用**,拿到就**绝不能改**——改了就绕过回滚、污染权威(后续命令失败时无法回退)。整份深拷模型没有这个坑(拿到的都是副本)。所以 `uRead`/`uWrite` 是**拿一条纪律换性能**,只在拷贝成本真的疼时才上。

> 现状:`UserState` 很小,用**默认整份深拷**即可,不引入 `uRead`/`uWrite`。本节是给「日后某实体长大」备的明确升级路径——升级只影响该实体的取用方式,不改 `reduce` 的逻辑形状。

## ③ delayDB 写回(概览,细节见 [db.md](db.md))

`reduce` 不碰 DB,只产出 `Persist`(快照值);GameLoop 把它同步写进**写缓冲**,由唯一的 **PersistWriter** 协程周期批量落库。两类写:

- **状态写**(实体「现在的样子」,如积分):同键**覆盖、只落最新**。
- **事件写**(「发生过一件事」,如手牌记录):**逐条追加**,靠唯一键幂等。

落库失败只重试 + 落日志,**绝不投回滚命令**——内存权威是对的,DB 只是没追上。写缓冲双缓冲、失败回灌「更新者优先」、优雅关闭 drain 等机制见 [db.md](db.md)。

## 鉴权列写路径(delayDB 之外的同步直写)

**不是所有 DB 写都走 delayDB。** 上面的模型(内存权威 + delayDB 异步追平)只适用于**有内存副本的权威状态**(全局积分、手牌记录)。**鉴权列**(`hash_password`/`name`/`k_cur`/`k_prev`)**不进内存**(见 [user.md](user.md):不放 `UserState`)、**DB 即权威、无内存副本**——delayDB 的「内存先生效、崩溃丢窗口可接受」对它们不成立(密码改了却在崩溃窗内静默回退 = 用户拿新密码登不进,比丢几点积分严重)。故它们走**请求级 session 的同步 UPDATE、commit 后才回成功**([app/db/user_writes.py](../app/db/user_writes.py),首个消费者 = 改密码 `POST /user/password`,[changes/0064](refactor/changes/0064-p7-change-password.md))。

**这不破「PersistWriter 是 delayDB 唯一写者」**:鉴权列写与 delayDB **正交**——不同的写路径、不同的数据(DB 权威 vs 内存权威)。**无锁前提靠列不相交**:PersistWriter 的状态写是**定向列 UPDATE**(`SET points`,[db.md](db.md)/0028),鉴权列写是 `SET hash_password`(0064 改密)/ `SET nickname`(0065 CAS 改名)/ `SET k_cur/k_prev/…`(0066 轮换,可跨进程 CLI),各写**永不碰 `points` 列** ⇒ 无 lost-update、无需 `FOR UPDATE`。这些写全部极低频(人工/每周 cron 发起),dev sqlite 库级串行 / 生产 pg 行级 MVCC 足矣。**唯一带 `points` 的鉴权路写是 `issue_login` 的建新行 INSERT**(0066 首发)——不破前提:尚未发行的用户必不在 `world.users`(无内存在场),PersistWriter 不可能对该行产写;复用既有行(pre-P5 login-enable)时则刻意不动 `points`。

## 崩溃语义

单进程,非优雅崩溃带走全部内存状态:进行中手牌 + 缓冲里未 flush 的积分变更全丢。因是**积分非货币**、本规模手牌量小,**接受**;重启从 DB 载入积分初值即可(运行期内存为权威),无需对账。优雅关闭则必须 drain 把缓冲落干净(见 [db.md](db.md))。

## 契约速查(必须守住)

1. **内存权威**:实时判定一律读内存,绝不读 DB 做实时判断。
2. **载入只在 shell、只读一次、绝不重载已在内存的实体**;载入决策在 reduce,shell 不读 `world`。
3. **改状态只改工作副本**(整份深拷,或大实体的 `uWrite` 拷贝);失败丢弃即回滚。`uRead` 的活引用只读不改。
4. **core 不碰 DB**,只产出 `Persist`(快照值);载入/落库全在 shell。
5. **唯一 DB 写者 ⇒ 无行锁;优雅关闭前必须 drain。**
