"""GameLoop:工作副本 commit-or-discard + dispatch 路由 + 错误回发 + 异常归一(architecture.md)。
只验接线(core 规则已在 tests/core 覆盖)。"""

from app.core.commands import BuyIn, SitDown
from app.core.enums import UserStatus
from app.core.domain import UserState
from app.shell import gameloop as gameloop_mod
from app.wire.server import ErrorMessage, UserStatusChanged
from tests.builders import make_world, room_with
from tests.shell._fakes import Shell, drain


def _two_watchers():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING, "bob": UserStatus.WATCHING})},
        users={
            "alice": UserState(uid=1, nickname="alice", points=500, room="r1"),
            "bob": UserState(uid=2, nickname="bob", points=500, room="r1"),
        },
    )


def test_success_commits_and_broadcasts_to_room_members():
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice", "bob")
    sh.gameloop.handle(SitDown(origin="alice", seat=0))
    # 提交:world 真的变了(alice 就座)
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN
    assert world.rooms["r1"].seats[0] is not None
    # 广播:房内每个有连接的成员都收到 UserStatusChanged
    for nick in ("alice", "bob"):
        msgs = drain(conns[nick])
        assert len(msgs) == 1 and isinstance(msgs[0], UserStatusChanged)
        assert msgs[0].status is UserStatus.SITTING_IN and msgs[0].seat_position == 0


def test_failure_discards_and_returns_error_to_origin_only():
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice", "bob")
    sh.gameloop.handle(SitDown(origin="alice", seat=99))  # 越界座位 → Err(NOT_YOUR_SEAT)
    # 回滚:world 一字节未动(commit-or-discard:失败臂不 commit)
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING
    assert all(s is None for s in world.rooms["r1"].seats)
    # 错误只回发起人,不广播
    a = drain(conns["alice"])
    assert len(a) == 1 and isinstance(a[0], ErrorMessage) and a[0].code.value == "NOT_YOUR_SEAT"
    assert drain(conns["bob"]) == []


def test_persist_routed_to_buffer():
    world = _two_watchers()
    sh = Shell(world)
    sh.connect("alice")
    sh.gameloop.handle(SitDown(origin="alice", seat=0))  # 就座(无 Persist)
    assert len(sh.persist) == 0
    sh.gameloop.handle(BuyIn(origin="alice", seat=0, amount=50))  # 买入产 Persist(PointsWrite)
    assert len(sh.persist) == 1  # PointsWrite 进了写缓冲


def test_reduce_exception_normalized_to_internal(monkeypatch):
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice")

    def boom(work, cmd):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(gameloop_mod, "reduce", boom)
    sh.gameloop.handle(SitDown(origin="alice", seat=0))
    # 异常:丢工作副本、world 未动 + 回发 INTERNAL
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING
    a = drain(conns["alice"])
    assert len(a) == 1 and isinstance(a[0], ErrorMessage) and a[0].code.value == "INTERNAL"
