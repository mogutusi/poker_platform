"""动态房间生命周期(0049,core.md §房间生命周期):谁都可创建(进房即建)+ 空房即销毁。

建房:JoinRoom 到不存在的房 + 带 create 配置 → reduce 建空房再加入(commit 插入 world.rooms)。
销毁:LeaveRoom / Cleanup / 手尾 _finalize_hand 驱逐使房 users_in_room 变空 → reduce 置 work.room=None → commit 销毁。
一处顶层归一守「已提交的房永不为空」,覆盖所有清空路径;销毁前先退座位筹码回全局(守恒)、HandRecord 仍落库。
"""

from app.core.commands import Cleanup, JoinRoom, LeaveRoom, RoomCreate
from app.core.domain import UserState
from app.core.enums import PlayerStatus, UserStatus
from app.core.events import Persist, Personal
from app.core.records import HandRecordWrite, PointsWrite
from app.wire.server import UserLeft
from tests.builders import card, hand_world, make_table, make_world, player, run, seat


# ── 建房:房不存在 + 带 create → 建空房(PENDING_START)+ 加入观战 + 落定 world.rooms ──
def test_join_creates_room_when_absent():
    world = make_world()  # 空 world,无任何房
    world, ev, err = run(
        world,
        JoinRoom(origin="C", room="new", uid=9, loaded=100, create=RoomCreate(small_blind=5, buy_in=200, seats=4)),
    )
    assert err is None
    assert "new" in world.rooms  # 建房落定
    room = world.rooms["new"]
    assert (room.small_blind, room.buy_in, len(room.seats)) == (5, 200, 4)  # 用 create 配置
    assert room.users_in_room == {"C": UserStatus.WATCHING}  # 创建者以观战入房(无特权)
    assert world.users["C"].room == "new" and world.users["C"].points == 100
    assert not any(isinstance(e, Persist) for e in ev)  # 建房+进房不动积分/不落库


# ── 销毁:最后一人 LeaveRoom → 房销毁 + 本人回大厅 + 收 UserLeft 回执 ──
def test_leave_last_user_destroys_room():
    world = make_world()
    world, _, _ = run(
        world,
        JoinRoom(origin="C", room="new", uid=9, loaded=100, create=RoomCreate(small_blind=1, buy_in=100, seats=4)),
    )
    assert "new" in world.rooms
    world, ev, err = run(world, LeaveRoom(origin="C"))
    assert err is None
    assert "new" not in world.rooms  # 最后一人离开 → 空房销毁
    assert "C" not in world.users  # 回大厅
    assert any(isinstance(e, Personal) and isinstance(e.msg, UserLeft) for e in ev)  # 回执给本人


# ── 不销毁:非最后一人离开 → 房留存(仍有成员)──
def test_leave_non_last_keeps_room():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)  # A 就座 r1
    world.users["C"] = UserState(uid=99, nickname="C", points=0, room="r1")
    world.rooms["r1"].users_in_room["C"] = UserStatus.WATCHING  # C 观战 r1
    world, ev, err = run(world, LeaveRoom(origin="C"))
    assert err is None
    assert "r1" in world.rooms  # A 仍在 → 房留存
    assert "C" not in world.users and "A" in world.users


# ── 销毁:Cleanup 清最后一个 OFFLINE 占座者 → 退筹回全局(守恒)后销毁 ──
def test_cleanup_last_offline_destroys_room_retiring_chips():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)  # A 就座 r1,座位 100 筹码
    world.rooms["r1"].users_in_room["A"] = UserStatus.OFFLINE  # A 断线保座
    world, ev, err = run(world, Cleanup(origin=None, nick="A"))
    assert err is None
    assert "r1" not in world.rooms  # 最后一人清理 → 房销毁
    assert "A" not in world.users
    # 退座位筹码回全局先于销毁:PointsWrite 记 A 退筹后的全局积分(守恒,座位 100 → 全局)
    pw = [e.payload for e in ev if isinstance(e, Persist) and isinstance(e.payload, PointsWrite)]
    assert pw and pw[0].points == 100


# ── 销毁:手尾 _finalize_hand 驱逐使房变空 → 销毁;HandRecord 仍落库(Persist 与房存亡无关)──
def test_finalize_eviction_empties_room_destroys_it():
    # A 全押不能弃(ALLIN)+ B 行动中;两人都请求离桌 → B auto-fold 结束本手(fold-to-one,A 无争议赢)→
    # 手尾同时驱逐 A(leaving)+ B(leaving)→ 房空销毁。
    world = hand_world(
        [
            player("A", 0, seat=0, status=PlayerStatus.ALLIN, hole=(card("As"), card("Ks"))),
            player("B", 40, seat=1, hole=(card("Qh"), card("Jc"))),
        ],
        acting_position=1,
        contributed={"A": 20, "B": 20},
    )
    world, _, err = run(world, LeaveRoom(origin="A"))  # A ALLIN 不能弃 → 仅标 leaving,手继续
    assert err is None and "r1" in world.rooms and world.rooms["r1"].hand is not None
    world, ev, err = run(world, LeaveRoom(origin="B"))  # B 行动中 → auto-fold → 手结束 → 手尾驱逐 A+B
    assert err is None
    assert "r1" not in world.rooms  # 手尾两人皆驱逐 → 房空销毁
    assert "A" not in world.users and "B" not in world.users
    assert any(isinstance(e, Persist) and isinstance(e.payload, HandRecordWrite) for e in ev)  # 记录仍落库
