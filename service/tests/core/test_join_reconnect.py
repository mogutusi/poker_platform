"""P1 余项:JoinRoom 进房 + Connect 重连恢复 + StateSnapshot 整桌快照 —— lobby.md / user.md / connection.md。

JoinRoom:大厅→房间,装 world.users 为 WATCHING + Broadcast(UserJoined) + Personal(StateSnapshot)。
Connect:OFFLINE → 按 world 推断恢复(PLAYING/SITTING_IN/WATCHING)+ 广播 + 私发快照;在房在线(顶替再连,0031)→ 只私发快照对齐;纯大厅 → no-op。
StateSnapshot 隐私:your_hole_cards 仅收件人自己;players 投影无 hole_cards(他人底牌结构性缺位)。
"""

from app.core.commands import Connect, JoinRoom
from app.core.domain import UserState
from app.core.enums import HandStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, Persist, Personal
from app.wire.server import StateSnapshot, UserJoined
from tests.builders import card, hand_world, make_table, player, run, seat


def _room(world, name="r1"):
    return world.rooms[name]


def _snapshot(events):
    return next(e.msg for e in events if isinstance(e, Personal) and isinstance(e.msg, StateSnapshot))


def _cards_in(obj) -> set[tuple[str, str]]:
    # 递归收集序列化产物里所有 {rank,suit} 牌(用于值级隐私断言:他人具体牌面不得出现)。
    if isinstance(obj, dict):
        if set(obj) == {"rank", "suit"}:
            return {(obj["rank"], obj["suit"])}
        out: set[tuple[str, str]] = set()
        for v in obj.values():
            out |= _cards_in(v)
        return out
    if isinstance(obj, (list, tuple)):
        out = set()
        for v in obj:
            out |= _cards_in(v)
        return out
    return set()


def _add_watcher(world, nick, *, uid=99, room_name="r1"):
    world.users[nick] = UserState(uid=uid, nickname=nick, points=0, room=room_name)
    world.rooms[room_name].users_in_room[nick] = UserStatus.WATCHING
    return world


def _active_hand_world():
    # 进行中手牌:A(As Kd)、B(Qh Jc),FLOP,各已投 10
    return hand_world(
        [
            player("A", 50, seat=0, hole=(card("As"), card("Kd"))),
            player("B", 40, seat=1, hole=(card("Qh"), card("Jc"))),
        ],
        status=HandStatus.FLOP,
        flop=(card("2c"), card("7d"), card("9s")),
        contributed={"A": 10, "B": 10},
    )


# ── 进房(两手之间):装 UserState(uid/loaded)+ WATCHING + UserJoined 广播 + 快照私发 ──
def test_join_room_installs_watcher_and_snapshots():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world, ev, err = run(world, JoinRoom(origin="C", room="r1", uid=99, loaded=500))
    assert err is None
    room = _room(world)
    assert "C" in world.users and world.users["C"].room == "r1" and world.users["C"].points == 500
    assert world.users["C"].uid == 99  # 按 DB 读出的不可变主键装入
    assert room.users_in_room["C"] is UserStatus.WATCHING
    assert any(isinstance(e, Broadcast) and isinstance(e.msg, UserJoined) and e.msg.nickname == "C" for e in ev)
    snap = _snapshot(ev)
    assert snap.your_hole_cards is None  # 观战者无自有底牌
    assert {s.nickname for s in snap.seats} == {"A", "B"} and snap.max_seats == 6
    assert "C" in snap.watchers
    assert snap.hand_status is None and snap.players == ()  # 两手之间无手
    assert not any(isinstance(e, Persist) for e in ev)  # 进房不动积分/不落库


# ── 进房(局中):观战者看到公共面(board/pot/players),但看不到任何底牌 ──
def test_join_room_mid_hand_watcher_sees_public_state_only():
    world = _active_hand_world()
    world, ev, err = run(world, JoinRoom(origin="C", room="r1", uid=7, loaded=300))
    assert err is None
    snap = _snapshot(ev)
    assert snap.your_hole_cards is None  # 观战者
    assert snap.hand_status is HandStatus.FLOP and len(snap.board) == 3
    assert {p.nickname for p in snap.players} == {"A", "B"}
    assert all(not hasattr(p, "hole_cards") for p in snap.players)  # 他人底牌结构性缺位
    assert snap.pot == 20  # contributed 10+10
    assert {s.seat_position: s.points for s in snap.seats} == {0: 50, 1: 40}  # seats 显本手剩余筹码
    # 值级隐私:观战者快照里无任何在手玩家的具体牌面
    assert _cards_in(snap.model_dump(mode="json")) == {("2", "c"), ("7", "d"), ("9", "s")}  # 仅公共牌
    assert not any(isinstance(e, Persist) for e in ev)


# ── 单房间约束:已在房者再 JoinRoom → ALREADY_IN_ROOM,world 不动 ──
def test_join_room_already_in_room():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world, ev, err = run(world, JoinRoom(origin="A", room="r1", uid=0, loaded=100))
    assert err is not None and err.code is ErrorCode.ALREADY_IN_ROOM
    assert ev == []


# ── 房间不存在 → NO_SUCH_ROOM,不装入 world.users ──
def test_join_room_no_such_room():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)
    world, ev, err = run(world, JoinRoom(origin="C", room="ghost", uid=9, loaded=100))
    assert err is not None and err.code is ErrorCode.NO_SUCH_ROOM
    assert ev == [] and "C" not in world.users


# ── 重连(局中):OFFLINE → PLAYING 恢复 + 快照带自己底牌、不带他人底牌 ──
def test_reconnect_in_hand_restores_playing_with_own_cards():
    world = _active_hand_world()
    _room(world).users_in_room["A"] = UserStatus.OFFLINE  # A 断线(座位/筹码保留)
    world, ev, err = run(world, Connect(origin=None, nick="A"))
    assert err is None
    assert _room(world).users_in_room["A"] is UserStatus.PLAYING  # 恢复在局
    snap = _snapshot(ev)
    assert snap.your_hole_cards == (card("As"), card("Kd"))  # 仅自己的底牌
    assert snap.hand_status is HandStatus.FLOP and len(snap.board) == 3
    assert snap.pot == 20 and snap.acting_position == 0
    # 值级隐私(非恒真式):序列化产物里只出现「我自己的底牌 + 公共牌」,对手 B 的 Qh/Jc 绝不出现
    serialized = _cards_in(snap.model_dump(mode="json"))
    assert ("Q", "h") not in serialized and ("J", "c") not in serialized  # 对手底牌不泄露
    assert serialized == {("A", "s"), ("K", "d"), ("2", "c"), ("7", "d"), ("9", "s")}  # 自己 + 公共牌
    assert all(not hasattr(p, "hole_cards") for p in snap.players)  # players 结构上亦无底牌字段
    # 广播 UserStatusChanged(A→PLAYING)给全房;重连不动积分/不落库
    assert any(isinstance(e, Broadcast) and getattr(e.msg, "nickname", None) == "A" for e in ev)
    assert not any(isinstance(e, Persist) for e in ev)


# ── 重连(有座、两手之间):OFFLINE → SITTING_IN(需重新 ready)+ 快照 ──
def test_reconnect_with_seat_no_hand_restores_sitting_in():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    _room(world).users_in_room["A"] = UserStatus.OFFLINE
    world, ev, err = run(world, Connect(origin=None, nick="A"))
    assert err is None
    assert _room(world).users_in_room["A"] is UserStatus.SITTING_IN
    snap = _snapshot(ev)
    assert snap.your_hole_cards is None and snap.hand_status is None
    assert not any(isinstance(e, Persist) for e in ev)


# ── 重连(局中但本手未发牌:有座、非 Player → SITTING_IN)──
def test_reconnect_seated_but_not_in_hand_restores_sitting_in():
    world = _active_hand_world()  # A、B 在手
    room = _room(world)
    room.seats[2] = seat("C", 60, new_here=False)  # C 有座,但本手未被发牌(如断线前坐出)
    world.users["C"] = UserState(uid=5, nickname="C", points=0, room="r1")
    room.users_in_room["C"] = UserStatus.OFFLINE
    world, ev, err = run(world, Connect(origin=None, nick="C"))
    assert err is None
    assert _room(world).users_in_room["C"] is UserStatus.SITTING_IN  # 有座、不在手 → SITTING_IN(非 PLAYING)
    snap = _snapshot(ev)
    assert snap.your_hole_cards is None  # C 不在手 → 无自有底牌
    assert snap.hand_status is HandStatus.FLOP  # 但快照含进行中手牌的公共面
    assert not any(isinstance(e, Persist) for e in ev)


# ── 重连(无座观战者):OFFLINE → WATCHING ──
def test_reconnect_no_seat_restores_watching():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world = _add_watcher(world, "W")
    _room(world).users_in_room["W"] = UserStatus.OFFLINE
    world, ev, err = run(world, Connect(origin=None, nick="W"))
    assert err is None
    assert _room(world).users_in_room["W"] is UserStatus.WATCHING
    assert _snapshot(ev).your_hole_cards is None
    assert not any(isinstance(e, Persist) for e in ev)


# ── 顶替再连(在房在线,新 ws 接管旧连接):只私发 StateSnapshot 对齐新连接,状态不变、不广播(0031)──
def test_connect_online_in_room_resends_snapshot():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    world, ev, err = run(world, Connect(origin=None, nick="A"))  # A 在房在线(READY_TO_PLAY)→ 顶替再连
    assert err is None
    assert _room(world).users_in_room["A"] is UserStatus.READY_TO_PLAY  # 状态不变(无恢复 / 无转移)
    personals = [e for e in ev if isinstance(e, Personal)]
    assert len(personals) == 1 and personals[0].nick == "A" and isinstance(personals[0].msg, StateSnapshot)
    assert not any(isinstance(e, Broadcast) for e in ev)  # 顶替对他人无信息变化 → 不广播
    assert not any(isinstance(e, Persist) for e in ev)  # 不动积分 / 不落库
    snap = _snapshot(ev)
    assert snap.your_hole_cards is None and snap.hand_status is None  # 两手之间无手
    assert {s.nickname for s in snap.seats} == {"A", "B"}


# ── 顶替再连(局中,A 在手):快照带 A 自有底牌、不带对手底牌;留 PLAYING、不广播(0031)──
def test_connect_in_hand_takeover_carries_own_cards_not_others():
    world = _active_hand_world()  # A(As Kd)、B(Qh Jc) 在手 FLOP(hand_world 置 PLAYING)
    assert _room(world).users_in_room["A"] is UserStatus.PLAYING  # 前提:A 在线在局
    world, ev, err = run(world, Connect(origin=None, nick="A"))  # 顶替再连
    assert err is None
    assert _room(world).users_in_room["A"] is UserStatus.PLAYING  # 顶替不改状态
    assert not any(isinstance(e, Broadcast) for e in ev)  # 不广播
    snap = _snapshot(ev)
    assert snap.your_hole_cards == (card("As"), card("Kd"))  # 自己的底牌
    # 值级隐私(非恒真式):序列化产物里对手 B 的 Qh/Jc 绝不出现,只有自己 + 公共牌
    serialized = _cards_in(snap.model_dump(mode="json"))
    assert ("Q", "h") not in serialized and ("J", "c") not in serialized
    assert serialized == {("A", "s"), ("K", "d"), ("2", "c"), ("7", "d"), ("9", "s")}
    assert not any(isinstance(e, Persist) for e in ev)


# ── 大厅用户 Connect(不在任何房)→ core 无事(进房走 JoinRoom)──
def test_connect_lobby_user_is_noop():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)
    world, ev, err = run(world, Connect(origin=None, nick="Z"))  # Z 不在 world.users
    assert err is None and ev == []
