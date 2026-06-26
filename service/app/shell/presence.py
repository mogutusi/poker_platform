# 在线状态只读聚合(presence.md):「谁在线 / 在哪房 / 什么状态」给 lobby/messaging/rest/好友共用。
# 不是新权威态——在线来自 ConnectionManager、房/状态来自只读 committed world。绝不写 world、绝不实时游戏裁定
# (实时裁定一律在 reduce);只展示 / 软守门、容忍滞后一拍(不变量 2:其它协程只读已提交态、不撕裂)。

from app.core.domain import World
from app.shell.connection import ConnectionManager


class Presence:
    def __init__(self, world: World, conns: ConnectionManager) -> None:
        # world 对象稳定(commit 原地替换其 .users/.rooms,见 shell/world.py)⇒ 每次读得最新提交态、不持快照。
        # 不撕裂:唯一写者 GameLoop.handle 全程无 await(见 gameloop.py),任何协程都无法在 commit 两行赋值间切入,
        # 故只读消费者要么读到提交前、要么提交后的整份一致态(不变量 2)。
        self._world = world
        self._conns = conns

    def is_online(self, nick: str) -> bool:
        # 有 live 连接 = 在线;与「在房」正交(可在线在大厅、可在房但 OFFLINE)。
        return self._conns.get(nick) is not None

    def current_room(self, nick: str) -> str | None:
        # 在哪个房:world.users[nick].room;纯大厅用户不在 world.users → None(见 lobby.md)。
        user = self._world.users.get(nick)
        return user.room if user is not None else None

    def room_headcount(self, room: str) -> int:
        # 房里逻辑成员数(含观战 + OFFLINE 保座);房不存在 → 0。展示用,可滞后一拍。
        r = self._world.rooms.get(room)
        return len(r.users_in_room) if r is not None else 0

    def online_nicks(self) -> set[str]:
        # 全体在线 nick(ConnectionManager 全表)。
        return self._conns.online_nicks()
