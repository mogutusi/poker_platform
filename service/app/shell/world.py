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
    room_name: str | None  # target room key (None = pure-lobby command, no room)
    room: Room | None  # deep copy of target room; None = absent in world (reduce may create it)
    users: dict[str, UserState]  # deep copy of the whole users table


def _target_room(world: World, cmd: Command) -> str | None:
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
    world.users = work.users
    if work.room_name is None:
        return
    if work.room is None:
        world.rooms.pop(work.room_name, None)
    else:
        world.rooms[work.room_name] = work.room
