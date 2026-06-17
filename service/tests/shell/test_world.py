"""P0:工作副本 checkout/commit —— 目标房解析、跨命令隔离、失败回滚(storage.md)。"""

from app.core.commands import (
    Cleanup,
    Connect,
    JoinRoom,
    PlayerAction,
    SitDown,
)
from app.core.domain import UserState
from app.core.enums import PlayerActionType, RoomStatus
from app.shell import world as world_api
from tests.builders import make_world, room_with, seat


def _world_one_room():
    world = make_world(
        rooms={"r1": room_with(seats=[seat("A", 100)])},
        users={"A": UserState(uid=1, nickname="A", points=500, room="r1")},
    )
    return world


def test_checkout_resolves_room_from_user():
    # 不带 room 的 wire 命令:目标房由 world.users[origin].room 推定。
    world = _world_one_room()
    work = world_api.checkout(world, SitDown(origin="A", seat=0))
    assert work.room_name == "r1"
    assert work.room is not None


def test_checkout_joinroom_uses_command_room_even_if_absent():
    # JoinRoom 自带 room;房不存在 → 副本里 room 为 None,reduce 负责新建。
    world = make_world()
    work = world_api.checkout(world, JoinRoom(origin="A", room="r9", uid=1, loaded=500))
    assert work.room_name == "r9"
    assert work.room is None


def test_checkout_lobby_connect_has_no_room():
    # 纯大厅 Connect:nick 不在 users → 无目标房。
    world = make_world()
    work = world_api.checkout(world, Connect(origin=None, nick="A"))
    assert work.room_name is None
    assert work.room is None


def test_checkout_deepcopies_room_and_users():
    world = _world_one_room()
    work = world_api.checkout(world, SitDown(origin="A", seat=0))
    # 改副本不应触及权威。
    work.room.seats[0].points = 999
    work.users["A"].points = 0
    assert world.rooms["r1"].seats[0].points == 100
    assert world.users["A"].points == 500


def test_commit_replaces_room_reference():
    world = _world_one_room()
    work = world_api.checkout(world, PlayerAction("A", action=PlayerActionType.CHECK))
    work.room.status = RoomStatus.HAND_STARTED
    world_api.commit(world, work)
    assert world.rooms["r1"].status is RoomStatus.HAND_STARTED
    # commit 是替换引用:权威房 == 副本房。
    assert world.rooms["r1"] is work.room


def test_commit_or_discard_is_the_only_rollback():
    # commit-or-discard:回滚机制就是「不 commit」——失败臂改了副本却不落定,
    # 成功臂才落定。同一份被改副本对照证明:回滚不靠补偿动作,只靠没 commit。
    world = _world_one_room()
    work = world_api.checkout(world, SitDown(origin="A", seat=0))
    work.room.status = RoomStatus.HAND_STARTED
    work.users["A"].points = 0
    # 失败臂:不 commit ⇒ world 一字节没动。
    assert world.rooms["r1"].status is RoomStatus.PENDING_START
    assert world.users["A"].points == 500
    # 成功臂:commit 后同一份改动才落定。
    world_api.commit(world, work)
    assert world.rooms["r1"].status is RoomStatus.HAND_STARTED
    assert world.users["A"].points == 0


def test_commit_creates_new_room():
    world = make_world()
    work = world_api.checkout(world, JoinRoom(origin="A", room="r9", uid=1, loaded=500))
    # reduce 在副本上新建房。
    work.room = room_with(seats=[seat("A", 0)])
    world_api.commit(world, work)
    assert "r9" in world.rooms


def test_commit_destroys_empty_room():
    world = _world_one_room()
    work = world_api.checkout(world, Cleanup(origin=None, nick="A"))
    # reduce 判定房已空,置 None ⇒ commit 删除。
    work.room = None
    world_api.commit(world, work)
    assert "r1" not in world.rooms


def test_commit_replaces_users_table():
    world = _world_one_room()
    work = world_api.checkout(world, PlayerAction("A", action=PlayerActionType.CHECK))
    work.users["A"].points = 250
    world_api.commit(world, work)
    assert world.users["A"].points == 250
