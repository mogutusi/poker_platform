"""presence:REST 够到 world 的唯一合规窗口(presence.md)——只答「在哪个房」,只读已 commit 的态。"""

from app.core.commands import JoinRoom, LeaveRoom
from app.core.domain import UserState
from app.core.enums import UserStatus
from app.shell.presence import Presence
from tests.builders import make_table, make_world, room_with, run, seat


def _world_two_in_room():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING, "bob": UserStatus.SITTING_IN})},
        users={
            "alice": UserState(uid=1, nickname="alice", points=500, room="r1"),
            "bob": UserState(uid=2, nickname="bob", points=500, room="r1"),
        },
    )


def test_current_room_lobby_vs_in_room():
    p = Presence(_world_two_in_room())
    assert p.current_room("alice") == "r1"  # 在房
    assert p.current_room("zoe") is None  # 不在 world.users(大厅用户或未知 nick 皆然)→ None


def test_presence_reflects_committed_world_changes():
    # 核心:Presence 持稳定 world 引用;commit 原地改其 .users/.rooms → presence 读到最新提交态(不持快照)。
    world = make_world(rooms={"r1": room_with(users_in_room={})}, users={})
    p = Presence(world)
    assert p.current_room("alice") is None
    world, _, err = run(world, JoinRoom(origin="alice", room="r1", uid=1, loaded=100))  # commit 装入 alice
    assert err is None
    assert p.current_room("alice") == "r1"  # presence 见提交后变化
    world, _, _ = run(world, LeaveRoom(origin="alice"))  # commit 驱逐
    assert p.current_room("alice") is None  # 又见最新态


def test_presence_does_not_mutate_world():
    # 只读契约:任何 presence 读都不改 world(对照前后深比较)。
    import copy

    world = make_table({0: seat("A", 100), 1: seat("B", 100)}, button=0)
    before = copy.deepcopy(world)
    p = Presence(world)
    p.current_room("A")
    assert world == before  # presence 纯只读,world 一字节未动
