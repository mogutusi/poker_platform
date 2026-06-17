"""工作副本读写 API:World.checkout(cmd) / commit(work)(storage.md)。

唯一的状态修改 + 回滚机制:GameLoop 处理每条命令前 `checkout` 出一份工作副本
(目标房 + users 表深拷),reduce 只改副本,成功 `commit`(房间增/删/替换 + users
表替换)、失败/异常整份丢弃 ⇒ world 要么全改、要么一字节不动。

放在 shell:checkout 要读 world.users 解析目标房(GameLoop 是唯一写者,读自己
已提交状态不破坏不变量);core 的 reduce 只收 Work。
"""

import copy
from dataclasses import dataclass, field

from app.core.commands import (
    Cleanup,
    Command,
    Connect,
    Disconnect,
    JoinRoom,
    Timeout,
)
from app.core.domain import Room, UserState, World


@dataclass
class Work:
    """一条命令的工作副本:目标房(可能为 None)+ 全局 users 表(整份深拷)。

    reduce 在此原地改:`room` 可被新建 / 置 None(销毁);`users` 增删改。
    `room_name` 是目标房键(用于 commit 落回 world.rooms);None 表示纯大厅命令。
    """

    room_name: str | None
    room: Room | None
    users: dict[str, UserState]
    room_existed: bool = field(default=False)  # checkout 时该房是否已在 world(供 commit 区分新建/替换)


def _target_room(world: World, cmd: Command) -> str | None:
    """按命令类型解析目标房键(storage.md 的解析表)。

    - JoinRoom:命令自带 room(房可能不存在)。
    - 其余 wire 命令 / Timeout / Cleanup / Connect / Disconnect:取 world.users[origin].room。
      不在 users(纯大厅)→ None,无房可拷。
    """
    if isinstance(cmd, JoinRoom):
        return cmd.room
    if isinstance(cmd, (Connect, Disconnect, Timeout, Cleanup)):
        nick = cmd.nick
    else:
        nick = cmd.origin
    if nick is None:
        return None
    user = world.users.get(nick)
    return user.room if user is not None else None


def checkout(world: World, cmd: Command) -> Work:
    """深拷「目标房 + users 表」成工作副本。纯大厅命令无目标房,只拷 users。"""
    room_name = _target_room(world, cmd)
    existing = world.rooms.get(room_name) if room_name is not None else None
    return Work(
        room_name=room_name,
        room=copy.deepcopy(existing),
        users=copy.deepcopy(world.users),
        room_existed=existing is not None,
    )


def commit(world: World, work: Work) -> None:
    """把工作副本相对权威的差异整体落回 world(替换引用 ⇒ 跨命令隔离,不变量 7)。

    - users 表:始终整份替换。
    - 房间:reduce 建了新房 / 改了房 → 写回;reduce 置 room=None(销毁空房)→ 删除。
    """
    world.users = work.users
    if work.room_name is None:
        return
    if work.room is None:
        # 销毁:reduce 在副本上判定房已空,置 None。
        world.rooms.pop(work.room_name, None)
    else:
        # 新建或替换,统一写回引用。
        world.rooms[work.room_name] = work.room
