# world:GameLoop 工作副本的 checkout / commit(见 storage.md)。
# checkout(world, cmd):解析目标房 + 深拷贝(目标房 + users 表)→ Work;reduce 只改副本。
# commit(world, work):成功时把副本装回 world(替换引用,含房间增/删/替换 + users 表替换);失败不 commit = 回滚。

import copy

from app.core.commands import (
    Cleanup,
    Command,
    Connect,
    Disconnect,
    JoinRoom,
    Timeout,
)
from app.core.domain import Work, World

# Work 的类型定义已上移到 core/domain(reduce 的操作面,守「core 不 import shell」);
# checkout/commit 仍是 shell 的模块级函数,负责构造/落定工作副本(见 storage.md)。


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
