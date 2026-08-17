# 存储模型(内存权威 + 工作副本回滚 + delayDB)

## 一句话定位

所有状态都在内存 `world` 里,内存是权威,DB 只是滞后的持久层。

命令进来时,GameLoop 先深复制一份**工作副本**交给 `reduce` 改:成功就装回内存,失败整份丢弃——丢弃即回滚。

需要落库的改动以 `Persist` 事件交给 delayDB——「内存先生效、DB 稍后追平」的滞后写通道,机制见 [db.md](db.md)。

> 前置:[architecture.md](architecture.md)(分层、不变量、GameLoop)。

## ① 内存权威 + 载入一次

**载入一次**

- 需持久化的数据从 DB 读一次进内存,此后内存即权威,实时判定不读 DB。当前范围:全局积分;手牌结束时写手牌记录。
- 读 DB 是 IO,只在 shell 做(Receiver / lifespan);读到的值随命令带进 core(如 `Connect(loaded=...)`),core 内不 `await` DB。

**绝不重载已在内存的实体**

内存比 DB 新,重载会丢掉尚未落库的变更。是否安装由 reduce 判定(见 [user.md](user.md));shell 不读 `world`(不变量 2)。

**载入屏障(0073)**

「载入屏障」= 载入前强制让 DB 追平内存的关卡。上一条只保护「仍在内存里」的实体:实体被驱逐出内存后,若退分写还压在写缓冲里,同 nick 立刻重进就会读到滞后的 DB(0072·N1 lost-update)。

所以 `JoinRoom` 载入前,Receiver 先过两步再读 DB:

1. `inbox.join()`:等已入队的命令处理完,退分的 `Persist` 才会进缓冲。只靠 flush 堵不住「join 帧先于 LeaveRoom 被处理」的顺序问题。
2. `PersistWriter.barrier()`:把缓冲强制落库。

任一步失败都 fail-closed 回 `INTERNAL`,不拿可能陈旧的值装权威。见 [db.md](db.md)「运行期落库屏障」/ [changes/0073](refactor/changes/0073-persist-barrier-join-load.md)。

**启动初始化例外**

进程启动时内存为空,lifespan 直读 DB 初始化 `world` 是允许的(典型:种子用户进 DB);「绝不重载」针对的是运行期已安装的实体。

用户积分一律在 `JoinRoom` 时载入(per-join,[0030](refactor/changes/0030-p4-per-join-wire-load.md)),取代了 [0029](refactor/changes/0029-p4-db-backed-dev-shell.md) 的启动期整体载入。

动态房([0049](refactor/changes/0049-dynamic-rooms.md))没有静态预置:启动时 `world.rooms` 为空,房随 `JoinRoom` 而建、空房即销毁;房内一切(含 `chat_history`)随房消亡,这是有意语义(0071)。

**好处**

买入这类高频操作是纯内存转账,GameLoop 不需要 `await` DB;无并发写者,故无行锁、无 `with_for_update`。

## ② 工作副本回滚(进业务就深复制一份)

这是唯一的状态修改 + 回滚机制,所有命令一视同仁:

1. 处理命令前,把工作集深复制成工作副本。每条命令只作用于一个房间(不变量 8),所以工作集 = 目标房间 + 全局 `users` 表(默认整份拷,见下「大实体优化」)。
2. `reduce` 只改工作副本,校验与修改可以穿插。
3. `reduce` 返回 `(events, err)`:
   - 失败或抛异常 → 丢弃副本,`world` 一字节没动。
   - 成功 → 副本装回 `world`(替换引用),再 dispatch events(含 `Persist`)。

`checkout` / `commit` 是 `shell/world.py` 的模块级函数,接收 `world` 作首参,构造 / 落定一个 `Work`(`room_name` / `room` / `users`)。它们是模块级函数而不是方法,因为 `World` 是 core 纯 dataclass、不挂方法(见 [models.md](models.md))。`Work` 定义在 `core/domain.py`:它是 reduce 的操作面,放 shell 会让 core import shell(合法方向只有 shell→core)。见 [changes/0010](refactor/changes/0010-p1-reduce-start-hand.md)。

```python
# GameLoop 主循环(简化)
work = checkout(world, cmd)            # 解析目标房 + 深复制成工作副本
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

模型 2 = 一个用户同时只在一个房间,连接绑 nick 不绑房间,因此命令大多不带 room。`checkout` 接收整条 `cmd`、按类型解析出目标房,再深拷「该房 + `users` 表」。

GameLoop 读 `world.users` 解析房间是允许的:它是唯一写者、单协程,读的是自己的已提交状态。不变量 2 禁的是另外两件事——其它协程写 `world`、shell 读 `world` 做载入决策。

| 命令 | 目标房解析 | 副本里有没有房 |
|---|---|---|
| `JoinRoom(room, …)` | 命令自带 `room` | 房可能不存在 → 给「无此房」的副本,reduce 负责新建 |
| 其余 wire 命令(`PlayerAction`/`SitDown`/`BuyIn`/`RoomChat`/…) | `world.users[cmd.origin].room` | 必有(用户已在房) |
| `Timeout(nick)` / `Cleanup(nick)` | `world.users[nick].room`;不在 `users` 则无房,reduce no-op | 视情况 |
| `Connect(nick)` / `Disconnect(nick)` | `nick` 在 `world.users` → 其 `room`;否则纯大厅,无房可拷 | 视情况 |

> 纯大厅的 `Connect`/`Disconnect` 没有目标房,`checkout` 只拷 `users` 表(或连 users 都不动)。reduce 对它们「core 无事」,或只动 presence(在 shell)。

### `commit(world, work)`:房间的增 / 删 / 替换都在这里落定

`commit` 把工作副本相对权威的差异整体落回 `world.rooms` 顶层 dict:

- **替换**:目标房存在且被改 → `world.rooms[room] = work.room`。替换的是引用,旧对象不再被原地改,跨命令隔离(不变量 7)。
- **新建**:reduce 在副本上建了新房(`JoinRoom` 到不存在的房)→ 插入。
- **销毁**:reduce 在副本上 `del` 了空房(最后一人离开)→ `del world.rooms[room]`。
- **users 表**:始终整份替换。小实体默认整份拷;大了改用 `uRead`/`uWrite`,见下。

房间生命周期的精确时机见 [core.md](core.md)。销毁房后不要再 `Broadcast` 到它(见 [connection.md](connection.md) 的 dispatch 容错)。

收益三条:

- **失败安全**:没 commit 就等于没发生。校验失败和未预期异常走同一条路,无需补偿动作。
- **不变量 7 减负**:commit 替换引用后,已提交对象不再被原地改,所以纪律只剩「同一条 reduce 内,产出 event 后别再改它引用的对象」。
- **开销可忽略**:玩家 ≤ 20,深拷极小。

## 大实体优化:整份深拷 vs `uRead` / `uWrite`

现状 `UserState` 很小(昵称 + 积分),默认整份深拷,不引入 `uRead`/`uWrite`。本节是日后某实体「整份深拷开销可观、且多数命令只读它」时的升级路径:它只改该实体的取用方式,不改 `reduce` 的逻辑形状。

| 取法 | 给什么 | 用在 | 成本 |
|---|---|---|---|
| `uRead(work, key)` | 实体的活引用(只读) | 命令只读该实体 | 零拷贝 |
| `uWrite(work, key)` | 该实体的深拷贝 | 命令要改该实体 | 只拷这一个 |

`uWrite` 拿到的拷贝会登记进工作副本的待写表,收尾仍由 GameLoop 统一:成功 → 待写表里的拷贝装回权威 + 产出对应 `Persist`,失败 → 丢弃;语义和整份深拷完全一致。

按实体类型选一次,不混用。`uRead` 的唯一纪律:它给的是活引用,拿到就不能改——改了就绕过回滚、污染权威,后续命令失败时无法回退。

## ③ delayDB 写回(概览,细节见 [db.md](db.md))

`reduce` 不碰 DB,只产出 `Persist`(快照值)。GameLoop 把它同步写进写缓冲,由唯一的 PersistWriter 协程周期批量落库。

两类写:

- **状态写**:实体「现在的样子」,如积分。同键覆盖、只落最新。
- **事件写**:「发生过一件事」,如手牌记录。逐条追加,靠唯一键幂等。

落库失败只重试 + 落日志,绝不投回滚命令——内存权威是对的,DB 只是没追上。

## 鉴权列写路径(delayDB 之外的同步直写)

上面的模型只适用于有内存副本的权威状态。鉴权列(`hash_password`/`name`/`k_cur`/`k_prev`)不进内存(见 [user.md](user.md)),DB 就是权威:「内存先生效、崩溃丢窗口可接受」对它们不成立——密码改了却在崩溃窗内回退,用户拿新密码登不进,比丢几点积分严重。

所以它们走请求级 session 的同步 UPDATE,commit 后才回成功。实现在 [app/db/user_writes.py](../app/db/user_writes.py);首个消费者是改密码 `POST /user/password`([changes/0064](refactor/changes/0064-p7-change-password.md))。

**为什么不与「PersistWriter 是 delayDB 唯一写者」冲突**

两条写路径的列不相交:

- PersistWriter 的**状态写**是定向列 UPDATE(`SET points`,[db.md](db.md)/0028)。
- 鉴权列写是 `SET hash_password`(0064 改密)、`SET nickname`(0065 CAS 改名)、`SET k_cur/k_prev/…`(0066 轮换,可跨进程 CLI),都不碰 `points`,所以无 lost-update、无需 `FOR UPDATE`。

而且它们全部极低频(人工触发 / 每周 cron):dev 的 sqlite 库级串行、生产 pg 的行级 MVCC,都足够。

**唯一带 `points` 的鉴权路写**:`issue_login` 建新行的 INSERT(0066 首发)。尚未发行的用户必定不在 `world.users`,PersistWriter 不会写该行;复用既有行时(pre-P5 login-enable)刻意不动 `points`。

## 崩溃语义

单进程,非优雅崩溃带走全部内存状态:进行中的手牌 + 缓冲里未 flush 的积分变更全丢。积分非货币、手牌量小,接受;重启从 DB 载入积分初值即可,无需对账。

优雅关闭必须 drain 把缓冲落干净(见 [db.md](db.md))。

## 契约速查(必须守住)

1. **内存权威**:实时判定一律读内存,不读 DB。
2. **载入只在 shell、只读一次、绝不重载已在内存的实体**;载入决策在 reduce,shell 不读 `world`。驱逐后重进须先过载入屏障(`inbox.join()` + `barrier()`,0073)再读 DB,失败 fail-closed。
3. **改状态只改工作副本**(整份深拷,或大实体的 `uWrite` 拷贝);失败丢弃即回滚。`uRead` 的活引用只读不改。
4. **core 不碰 DB**,只产出 `Persist`(快照值);载入 / 落库全在 shell。
5. **唯一 DB 写者 ⇒ 无行锁;优雅关闭前必须 drain。**
