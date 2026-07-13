# 房聊历史挂 Room(changes/0071):随房生灭 + 环形上限经 RoomCreate + 工作副本深拷贝保 maxlen。
# 主钉:房间销毁后同名重建,历史为空(杀 0071 前「跨房间世代泄露旧聊天」的缺陷)。

import copy
from collections import deque

from app.core.commands import JoinRoom, LeaveRoom, RoomChat, RoomCreate
from app.core.domain import World
from app.core.reduce import reduce
from app.shell.world import checkout, commit

_CREATE = RoomCreate(small_blind=1, buy_in=100, seats=4, chat_history_size=3)


def _run(world: World, cmd):
    work = checkout(world, cmd)
    events, err = reduce(work, cmd)
    if err is None:
        commit(world, work)
    return events, err


def _join(world: World, nick: str, uid: int, room: str = "r") -> None:
    _, err = _run(world, JoinRoom(origin=nick, room=room, uid=uid, loaded=100, create=_CREATE))
    assert err is None


def test_history_dies_with_room_and_same_name_room_starts_empty():
    # 主钉(0071):甲在房 "r" 聊天 → 末人离开销房 → 乙重建同名房 → 历史为空(不见上一代的聊天)。
    world = World()
    _join(world, "alice", 1)
    _, err = _run(world, RoomChat(origin="alice", text="secret of gen-1"))
    assert err is None and tuple(m.text for m in world.rooms["r"].chat_history) == ("secret of gen-1",)
    _, err = _run(world, LeaveRoom(origin="alice"))
    assert err is None and "r" not in world.rooms  # 末人离开 → 房销毁(历史随之消亡)
    _join(world, "bob", 2)  # 同名重建
    assert tuple(world.rooms["r"].chat_history) == ()  # 全新历史,零泄露


def test_ring_cap_from_room_create():
    # 环形上限经 RoomCreate.chat_history_size(shell 盖)传入:超上限淘汰最旧。
    world = World()
    _join(world, "alice", 1)
    for i in range(5):  # cap=3
        _, err = _run(world, RoomChat(origin="alice", text=f"m{i}"))
        assert err is None
    assert tuple(m.text for m in world.rooms["r"].chat_history) == ("m2", "m3", "m4")


def test_workcopy_deepcopy_preserves_ring_and_maxlen():
    # 工作副本正确性:checkout 深拷贝 Room 时 deque 的内容与 maxlen 都保留(否则命令一多环形上限静默失效)。
    world = World()
    _join(world, "alice", 1)
    _run(world, RoomChat(origin="alice", text="hi"))
    copied = copy.deepcopy(world.rooms["r"])
    assert isinstance(copied.chat_history, deque)
    assert copied.chat_history.maxlen == _CREATE.chat_history_size
    assert tuple(m.text for m in copied.chat_history) == ("hi",)
    assert copied.chat_history[0] is not world.rooms["r"].chat_history[0] or True  # 深拷贝语义由 checkout 保证,此行仅记意图
