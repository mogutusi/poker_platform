import copy
from dataclasses import dataclass

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
    room_name: str | None  # 目标房键(None = 纯大厅命令,无房)
    room: Room | None  # 目标房深拷贝;None = world 中无此房(reduce 可新建)
    users: dict[str, UserState]  # 整份 users 表的深拷贝


def _target_room(world: World, cmd: Command) -> str | None:
    # 按命令类型解析目标房(见 storage.md):JoinRoom 自带 room;系统命令看 nick;
    # 其余 wire 命令看 origin。nick 不在 world.users(纯大厅)→ 无房。
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
    room_name = _target_room(world, cmd)
    existing = world.rooms.get(room_name) if room_name is not None else None
    return Work(
        room_name=room_name,
        room=copy.deepcopy(existing),
        users=copy.deepcopy(world.users),
    )


def commit(world: World, work: Work) -> None:
    world.users = work.users  # users 表整份替换
    if work.room_name is None:
        return
    if work.room is None:
        world.rooms.pop(work.room_name, None)  # reduce 置 None = 销毁空房
    else:
        world.rooms[work.room_name] = work.room  # 新建或替换,统一写回引用
