"""端到端冒烟:命令穿过 GameLoop → reduce → commit → dispatch → 各连接 outbound。
验「整条管线接通 + 广播/私发分流」,不重测牌局规则(core 已覆盖)。"""

from app.core.commands import PlayerAction, StartHand
from app.core.enums import PlayerActionType, RoomStatus
from app.wire.server import (
    HandEnded,
    HandStarted,
    HandStatusChanged,
    HoleCards,
    PlayerActed,
)
from app.core.records import HandRecordWrite
from tests.builders import DECK, T0, make_table, seat
from tests.shell._fakes import Shell, drain


def _heads_up_ready_table():
    # 两名已就座 ready 玩家(established),供开局冒烟。
    return make_table(
        {0: seat("alice", 100, new_here=False), 1: seat("bob", 100, new_here=False)},
        room_name="r1",
    )


def test_full_pipeline_start_hand_and_action():
    world = _heads_up_ready_table()
    sh = Shell(world)
    conns = sh.connect("alice", "bob")

    # 开局:固定牌堆注入(确定性);经 GameLoop 全程串起。
    sh.gameloop.handle(StartHand(origin="alice", seat=0, started_at=T0, deck=list(DECK)))
    assert world.rooms["r1"].status is RoomStatus.HAND_STARTED
    hand = world.rooms["r1"].hand
    assert hand is not None

    alice_msgs = drain(conns["alice"])
    bob_msgs = drain(conns["bob"])
    # 广播:两人都收到 HandStarted + HandStatusChanged(pre_flop)
    for msgs in (alice_msgs, bob_msgs):
        assert any(isinstance(m, HandStarted) for m in msgs)
        assert any(isinstance(m, HandStatusChanged) for m in msgs)
    # 私发:各自只收到**自己**的底牌(HoleCards 走 Personal)
    a_holes = [m for m in alice_msgs if isinstance(m, HoleCards)]
    b_holes = [m for m in bob_msgs if isinstance(m, HoleCards)]
    assert len(a_holes) == 1 and len(b_holes) == 1
    assert a_holes[0].cards != b_holes[0].cards  # 各得各的两张

    # 事件顺序:HandStarted 在自己的 HoleCards 之前(core.md §事件)
    a_types = [type(m).__name__ for m in alice_msgs]
    assert a_types.index("HandStarted") < a_types.index("HoleCards")

    # 当前行动者弃牌 → 只剩一人 → 手结束。验 PlayerActed + HandEnded 广播 + Persist 落缓冲。
    actor = hand.players[hand.acting_position].nickname
    sh.gameloop.handle(PlayerAction(origin=actor, action=PlayerActionType.FOLD))
    after = drain(conns["alice"]) + drain(conns["bob"])
    assert any(isinstance(m, PlayerActed) for m in after)
    assert any(isinstance(m, HandEnded) for m in after)
    assert any(isinstance(p, HandRecordWrite) for p in sh.persist.snapshot())
    assert world.rooms["r1"].status is RoomStatus.PENDING_START  # 手结束,回到待开局


def test_unknown_room_command_errors_to_origin():
    # 不在任何房的发起人:checkout 解析无房 → reduce 回 Err;经 GameLoop 回发本人。
    world = _heads_up_ready_table()
    sh = Shell(world)
    conns = sh.connect("carol")  # carol 不在 world.users(未在房)
    sh.gameloop.handle(StartHand(origin="carol", seat=0, started_at=T0, deck=list(DECK)))
    msgs = drain(conns["carol"])
    assert len(msgs) == 1 and type(msgs[0]).__name__ == "ErrorMessage"
