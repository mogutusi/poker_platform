"""presence:在线状态只读聚合(presence.md)——在线来自 ConnectionManager、房/状态来自只读 committed world。"""

from app.core.commands import JoinRoom, LeaveRoom
from app.core.domain import UserState
from app.core.enums import UserStatus
from app.shell.connection import ConnectionManager
from app.shell.presence import Presence
from tests.builders import make_table, make_world, room_with, run, seat
from tests.shell._fakes import make_conn


def _world_two_in_room():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING, "bob": UserStatus.SITTING_IN})},
        users={
            "alice": UserState(uid=1, nickname="alice", points=500, room="r1"),
            "bob": UserState(uid=2, nickname="bob", points=500, room="r1"),
        },
    )


def test_is_online_tracks_connections():
    conns = ConnectionManager()
    p = Presence(_world_two_in_room(), conns)
    assert p.is_online("alice") is False  # 无连接
    conns.register(make_conn("alice"))
    assert p.is_online("alice") is True and p.is_online("bob") is False  # 在线 ⊥ 在房


def test_current_room_lobby_vs_in_room():
    p = Presence(_world_two_in_room(), ConnectionManager())
    assert p.current_room("alice") == "r1"  # 在房
    assert p.current_room("zoe") is None  # 不在 world.users(大厅用户或未知 nick 皆然)→ None


def test_online_in_lobby_orthogonality():
    # 在线 ⊥ 在房:有连接但不在 world.users(在线在大厅)→ is_online True 且 current_room None。
    conns = ConnectionManager()
    conns.register(make_conn("zoe"))
    p = Presence(_world_two_in_room(), conns)
    assert p.is_online("zoe") is True and p.current_room("zoe") is None


def test_room_headcount_counts_members_and_unknown_room_zero():
    p = Presence(_world_two_in_room(), ConnectionManager())
    assert p.room_headcount("r1") == 2  # 含观战 + 就座
    assert p.room_headcount("ghost") == 0  # 房不存在 → 0


def test_online_nicks_is_connection_set():
    conns = ConnectionManager()
    conns.register(make_conn("alice"))
    conns.register(make_conn("bob"))
    assert Presence(_world_two_in_room(), conns).online_nicks() == {"alice", "bob"}


def test_presence_reflects_committed_world_changes():
    # 核心:Presence 持稳定 world 引用;commit 原地改其 .users/.rooms → presence 读到最新提交态(不持快照)。
    world = make_world(rooms={"r1": room_with(users_in_room={})}, users={})
    p = Presence(world, ConnectionManager())
    assert p.current_room("alice") is None and p.room_headcount("r1") == 0
    world, _, err = run(world, JoinRoom(origin="alice", room="r1", uid=1, loaded=100))  # commit 装入 alice
    assert err is None
    assert p.current_room("alice") == "r1" and p.room_headcount("r1") == 1  # presence 见提交后变化
    world, _, _ = run(world, LeaveRoom(origin="alice"))  # commit 驱逐
    assert p.current_room("alice") is None and p.room_headcount("r1") == 0  # 又见最新态


def test_presence_does_not_mutate_world():
    # 只读契约:任何 presence 读都不改 world(对照前后深比较)。
    import copy

    world = make_table({0: seat("A", 100), 1: seat("B", 100)}, button=0)
    before = copy.deepcopy(world)
    p = Presence(world, ConnectionManager())
    p.is_online("A"), p.current_room("A"), p.room_headcount("r1"), p.online_nicks()
    assert world == before  # presence 纯只读,world 一字节未动
