# 在线状态(presence)

## 定位

presence 是只读的「谁在线 / 在哪个房 / 什么状态」视图,不是新的权威状态,只是对两处已有状态的只读聚合:

| 问题 | 答案来源 |
|---|---|
| 在不在线? | ConnectionManager 的 `nick → Connection`(shell:有连接即在线) |
| 在哪个房 / 房里几个人? | `world.rooms` / `world.users[nick].room`(core 权威状态,只读已提交的那份) |
| 什么 UserStatus? | `world.rooms[r].users_in_room[nick]`(同上) |

消费方有四处:[lobby.md](lobby.md) 要房间人数,[messaging.md](messaging.md) 要私聊在线判断,[rest.md](rest.md) 要改昵称的在房判定,未来的好友功能也共用它。

## 读 world 是允许的(只读、展示用、可滞后)

消费者读已提交的 `world` 符合并发不变量 2。前提有三条:

1. 只读,绝不写。
2. 只用于展示或软判定。实时游戏裁定一律在 reduce 内做。
3. 容忍滞后一拍。`commit` 是替换引用,单线程下读到的要么是旧的、要么是新的,不会撕裂。

这与 [rest.md](rest.md) 的「REST 读房间人数」是同款约定,presence 的作用是把零散读法收口成统一只读 API。

已落地([changes/0037](refactor/changes/0037-presence.md)):[`Presence(world, conns)`](../app/shell/presence.py) 类,四个只读方法,持稳定 `world` 引用——安全,因为 `commit` 原地替换 `world` 的 `.users`/`.rooms`,每次读到的都是最新提交态。

```python
class Presence:                       # app/shell/presence.py
    def is_online(self, nick) -> bool:          return self._conns.get(nick) is not None
    def current_room(self, nick) -> str | None: return (u.room if (u := self._world.users.get(nick)) else None)
    def room_headcount(self, room) -> int:      return len(r.users_in_room) if (r := self._world.rooms.get(room)) else 0
    def online_nicks(self) -> set[str]:         return self._conns.online_nicks()
```

两个判定的定义:在线 = 有 live 连接;在房 = `current_room` 非 None,大厅用户不在 `world.users` 里(见 [lobby.md](lobby.md))。两者正交:可以在线但在大厅,也可以在房但 OFFLINE。

## 改昵称:在房判定 + 连接重挂(填 [rest.md](rest.md) 的坑)

改昵称仅当用户不在任何游戏房时允许,规则出处见 [lobby.md](lobby.md)/[rest.md](rest.md)。走 REST 而非 ws 命令,理由有二:改昵称更贴资料管理;未连接时也能改,那时只改库。

接口是 `POST /user/nickname`,加密信封见 [rest.md](rest.md)。handler 落地见 [0065](refactor/changes/0065-p7-change-nickname.md),`rename` 原语随 [0037](refactor/changes/0037-presence.md)。

同进程顺序做三步:

1. **判定**:`current_room(old_nick) is None`(人在大厅)才允许,否则 REST 403;判定只读已提交 world。`old_nick` 以 DB 为准,因为会话表可能滞后(0065 决策 1)。**`ErrorCode` 里并没有** `CANT_CHANGE_NICK_IN_ROOM` 这个成员;将来真开 ws 形态时再加。
2. **改库**:CAS 更新 DB `nickname`,条件是 `WHERE id=uid AND nickname=old_nick`,并发双改名只有一个赢、唯一约束兜住撞名;再 `SessionStore.rename_nickname` 更新该账号全部会话。大厅用户不在 `world.users`,所以这一步不触碰 core 权威键。
3. **连接重挂**:若 `old_nick` 此刻有 live 连接,用 `rekey(conn, new)` 重挂。**连接在全部 await 之后才查**(0083,那之后到 rekey 全程同步、对事件循环原子);0065 曾改成 await 前捕获,为的是防「窗内该键被并发 rename/顶替动过 → 误挂他人连接」,但那样又踩另一头:窗内本人被顶替时捕获的就是死对象,`rekey` 落 else 分支只改死对象的 `.nick`,活连接永久挂在旧键下、用户在线却收不到任何消息。两头都要堵,所以现在是「晚查 + 归属校验」:加密连接比会话账号名(一个账号可有多个会话,故不比对象),dev 明文连接比 `session_id`(dev 端点建连时盖成握手 nick);`rekey` 自身的 `is` 判定留作第二道。`rename(old, new)` 是按键版,保留给不涉并发窗口的调用方。

```python
def rename(self, old: str, new: str) -> None:        # ConnectionManager
    conn = self._by_nick.pop(old, None)
    if conn is not None:
        conn.nick = new
        self._by_nick[new] = conn
```

> 微小竞态(最坏后果,0065):判定读 world 可能滞后一拍,极小窗口可能放过一次改名。最坏情形是改名与同刻 `join_room` 精确交错:world 以旧名装入成员,连接键已改新名。后果是该成员收不到房间消息、命令 `NOT_IN_ROOM`、`Cleanup` 因 WATCHING≠OFFLINE 不清,形成幽灵占位直到重启(房不空就不销毁)。触发需同一用户同刻双发,UI 无此路径,≤20 友善用户可接受。要更严就把改名也经一次 reduce 守门,本规模不必,升级路径保留。

## 与架构契约(必须守住)

1. presence 是只读聚合:在线来自 ConnectionManager,房/状态来自只读 `world`;绝不写 world、不做实时游戏裁定,读 world 只为展示/软守门、容忍滞后一拍。
2. 改昵称仅限大厅(`current_room is None`),改后重挂连接 nick 键 + 更新 DB/会话表。

## 待定 / future

- **变化推送**:presence 改变(上下线/进出房)主动广播给大厅/好友。v1 按需查询(轮询)。
- **好友在线提醒**、**「正在房间 X 游戏中」展示**:基于本视图扩展。
