# 在线状态(presence)

## 定位

presence 是**只读的「谁在线 / 在哪个房 / 什么状态」视图**,给 [lobby.md](lobby.md)(房间人数)、[messaging.md](messaging.md)(私聊在线判断)、[rest.md](rest.md)(改昵称"是否在房"判定)、未来好友共用。它不是新的权威状态,而是**对已有两处状态的只读聚合**:

| 问题 | 答案来源 |
|---|---|
| **在不在线?** | ConnectionManager 的 `nick → Connection`(shell;有连接=在线) |
| **在哪个房 / 房里几个人?** | `world.rooms`/`world.users[nick].room`(core 权威;**只读已提交状态**) |
| **什么 UserStatus?** | `world.rooms[r].users_in_room[nick]`(同上,只读) |

## 读 world 是允许的(只读、展示用、可滞后)

presence 的"在哪个房/人数/状态"来自 `world`。**消费者读已提交的 `world` 是允许的**(并发不变量 2:其它协程只读已提交状态),前提:

- **只读、绝不写**;**只用于展示 / 软判定,不做实时游戏裁定**(实时裁定一律在 reduce 内)。
- **可滞后一拍**:`commit` 替换引用(单线程,读到的要么旧要么新,不撕裂);读者可能看到落后一条命令的房态,对展示/守门足够。
- 与 [rest.md](rest.md) 的"REST 读房间人数"同款约定——presence 把这些零散读法收口成统一只读 API。

**已落地([changes/0037](refactor/changes/0037-presence.md))**:收口成 [`Presence(world, conns)`](../app/shell/presence.py) 类(持稳定 `world` 引用——`commit` 原地替换其 `.users`/`.rooms`,故每次读得最新提交态)+ 四只读方法:

```python
class Presence:                       # app/shell/presence.py
    def is_online(self, nick) -> bool:          return self._conns.get(nick) is not None
    def current_room(self, nick) -> str | None: return (u.room if (u := self._world.users.get(nick)) else None)
    def room_headcount(self, room) -> int:      return len(r.users_in_room) if (r := self._world.rooms.get(room)) else 0
    def online_nicks(self) -> set[str]:         return self._conns.online_nicks()
```

> 在线 = 有 live 连接(ConnectionManager);在房 = `current_room` 非 None(大厅用户不在 `world.users`,见 [lobby.md](lobby.md))。两者正交:可在线在大厅、可在房但 OFFLINE。

## 改昵称:在房判定 + 连接重挂(填 [rest.md](rest.md) 的坑)

改昵称仅当**不在任何游戏房**(你定的规则,见 [lobby.md](lobby.md)/[rest.md](rest.md)):

1. **判定**:`current_room(nick) is None`(在大厅)才允许;否则 `Err(CANT_CHANGE_NICK_IN_ROOM)`。读已提交 world,只读。
2. **改库**:更新 DB `nickname`(唯一约束)+ 会话表的 `nickname`。大厅用户**不在 `world.users`**,所以不触碰 core 权威键。
3. **连接重挂**:若该 nick 此刻有 live 连接,把 ConnectionManager **从 `old_nick` 重挂到 `new_nick`**——`rename(old, new)`:移 dict 键 + 改 `Connection.nick`;否则私聊/路由按新 nick 找不到旧连接。

```python
def rename(self, old: str, new: str) -> None:        # ConnectionManager
    conn = self._by_nick.pop(old, None)
    if conn is not None:
        conn.nick = new
        self._by_nick[new] = conn
```

- **落点**:改昵称走 REST(见 [rest.md](rest.md)),REST handler 同进程调 `conns.rename` + 改 DB/会话表。`ConnectionManager.rename`(连接重挂原语)**已落地 [0037](refactor/changes/0037-presence.md)**;「仅大厅判定 + 改 DB/会话表」的 REST handler 待 P7 REST。**决策(可改)**:也可做成 ws 命令在 shell 处理;REST 更贴"资料管理",且未连接时也能改(只改库)。
- **微小竞态**:判定读 world 可能滞后一拍(刚 JoinRoom、REST 仍看到在大厅)→ 极小窗口可能放过一次改名。≤20 友善用户接受;要严就把改名也经一次 reduce 守门(本规模不必)。

## 与架构契约(必须守住)

1. presence 是**只读聚合**:在线来自 ConnectionManager,房/状态来自只读 `world`;**绝不写 world、绝不做实时游戏裁定**。
2. 改昵称仅限大厅(`current_room is None`),改后**重挂连接 nick 键** + 更新 DB/会话表。
3. 读 world 只为展示/软守门,容忍滞后一拍。

## 待定 / future

- **变化推送**:presence 改变(上下线/进出房)主动广播给大厅/好友——v1 按需查询(轮询)。
- **好友在线提醒**、**"正在房间 X 游戏中"展示**:基于本视图扩展。
