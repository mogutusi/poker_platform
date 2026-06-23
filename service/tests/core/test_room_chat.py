"""P1 余项:房间聊天 RoomChat(reduce `_room_chat`)—— messaging.md §房间聊天。

只读命令:校验发送者在房 → 产 Broadcast(ChatMessage{from_nick,text}),不改任何游戏状态。
文本非空/长度/限速归 shell 文本防护(本层不测)。
"""

import copy

from app.core.commands import RoomChat
from app.core.enums import UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, Persist
from app.wire.server import ChatMessage
from tests.builders import hand_world, make_table, player, run, seat


def _room(world, name="r1"):
    return world.rooms[name]


def _add_watcher(world, nick, *, uid=99, room_name="r1"):
    from app.core.domain import UserState

    world.users[nick] = UserState(uid=uid, nickname=nick, points=0, room=room_name)
    world.rooms[room_name].users_in_room[nick] = UserStatus.WATCHING
    return world


# ── 在房成员发言 → 广播 ChatMessage 给目标房;不改游戏状态、不落库 ──
def test_room_chat_broadcasts():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    before = (dict(_room(world).users_in_room), _room(world).hand, _room(world).status)

    world, ev, err = run(world, RoomChat(origin="A", text="nice hand"))
    assert err is None
    assert len(ev) == 1 and isinstance(ev[0], Broadcast)
    msg = ev[0].msg
    assert isinstance(msg, ChatMessage)
    assert msg.from_nick == "A" and msg.text == "nice hand"
    assert ev[0].room == "r1"  # 派发按 users_in_room,含全房(观战者亦收)
    # 只读:游戏状态一字未动、无 Persist
    after = (dict(_room(world).users_in_room), _room(world).hand, _room(world).status)
    assert after == before
    assert not any(isinstance(e, Persist) for e in ev)


# ── 观战者也能房聊(在房即可,无需就座)──
def test_watcher_can_chat():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world = _add_watcher(world, "W")
    world, ev, err = run(world, RoomChat(origin="W", text="gl all"))
    assert err is None and isinstance(ev[0].msg, ChatMessage) and ev[0].msg.from_nick == "W"


# ── 不在任何房间(大厅)发言 → NOT_IN_ROOM,无副作用 ──
def test_room_chat_not_in_room():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world, ev, err = run(world, RoomChat(origin="Z", text="hello"))  # Z 不在 world.users
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM
    assert ev == []


# ── 身份取连接绑定 origin,不信报文(text 任意字符串原样转发,隐私字段结构性缺位)──
def test_room_chat_passes_text_verbatim_no_privacy_fields():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world, ev, err = run(world, RoomChat(origin="A", text="  spaces & symbols 😀 "))
    assert err is None
    msg = ev[0].msg
    assert msg.text == "  spaces & symbols 😀 "  # 原样(非空/长度由 shell 把关)
    assert not hasattr(msg, "hole_cards") and not hasattr(msg, "deck")  # 聊天 DTO 结构上无游戏隐私


# ── 强只读守护:进行中手牌里发房聊 → Hand/座位/全局积分**深比较**一字未动(参与者发言亦然)──
def test_room_chat_readonly_during_active_hand():
    world = hand_world(
        [player("A", 50, seat=0, bet_amount=10), player("B", 40, seat=1, bet_amount=10)],
        contributed={"A": 20, "B": 20},
    )
    room_before = copy.deepcopy(world.rooms["r1"])  # 深快照:含 Hand 全字段、各 Seat、状态
    users_before = copy.deepcopy(world.users)

    world, ev, err = run(world, RoomChat(origin="A", text="ty"))  # 在局玩家发言
    assert err is None and isinstance(ev[0].msg, ChatMessage)
    assert world.rooms["r1"] == room_before  # 进行中手牌/座位/底池一字未动(深比较,非引用)
    assert world.users == users_before  # 全局积分未动
    assert not any(isinstance(e, Persist) for e in ev)


# ── 防御臂:用户在 world.users(room=r1)但不在 r1.users_in_room 的不一致态 → 仍 NOT_IN_ROOM ──
def test_room_chat_inconsistent_membership_not_in_room():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    del world.rooms["r1"].users_in_room["A"]  # A 的 UserState.room 仍指 r1,但已不在成员表(不一致)
    world, ev, err = run(world, RoomChat(origin="A", text="hi"))
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM  # 经第三分支 nick not in users_in_room
    assert ev == []
