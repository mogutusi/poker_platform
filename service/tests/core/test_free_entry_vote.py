"""P1 余项:免盲投票(reduce `_open_free_entry_vote` / `_vote_free_entry`)—— rules.md ①「免盲投票」+ 测试 ①.12-15。

投票人 = 已入局(非 new_here)且 READY_TO_PLAY 的座位;候选 = 当前 new_here 座位。
全票 approve(非空)→ 快照候选进 room.waive_entry_for(_start_hand 消费,免费正常入局);
任一 reject → 失败;投票人离场/坐出 → 重算(①.15)。投票不动积分/座位/底牌。SB=1、BB=2。
"""

from app.core.commands import (
    Connect,
    Disconnect,
    LeaveRoom,
    OpenFreeEntryVote,
    SetUserStatus,
    SitDown,
    StartHand,
    VoteFreeEntry,
)
from app.core.domain import UserState
from app.core.enums import UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, Personal, Persist
from app.wire.server import FreeEntryVoteClosed, FreeEntryVoteUpdated, StateSnapshot
from tests.builders import DECK, T0, make_table, run, seat

SB = 1
BB = 2


def _room(world, name="r1"):
    return world.rooms[name]


def _by_seat(hand, idx):
    return next(p for p in hand.players if p.seat_position == idx)


def _closed(events):
    # 取事件里的 FreeEntryVoteClosed(终结报文),无则 None。
    return next((e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, FreeEntryVoteClosed)), None)


def _updated(events):
    return next((e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, FreeEntryVoteUpdated)), None)


def _three_plus_newcomer(*, button=3):
    # A/B/C 已入局(投票人)READY + D 新人 READY(座 3);button=3 → 推进到 0、D=UTG(非盲位,便于断言免付)。
    return make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True),
        },
        button=button,
    )


def _add_watcher(world, nick, *, uid=99, room_name="r1"):
    # 在房观战者(WATCHING,无座位):用于「投票通过后才坐下」蹭车测试。
    world.users[nick] = UserState(uid=uid, nickname=nick, points=0, room=room_name)
    world.rooms[room_name].users_in_room[nick] = UserStatus.WATCHING
    return world


# ── ①.12 全票免盲:3 个已入局玩家全 approve → 新玩家进 waive_entry_for → 下一手免费正常入局 ──
def test_unanimous_approve_waives_newcomer():
    world = _three_plus_newcomer()

    world, ev, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    opened = _updated(ev)
    assert opened.candidates == ("D",) and opened.voters == ("A", "B", "C") and opened.approvals == ()
    assert _room(world).entry_vote is not None and _room(world).entry_vote.approvals == set()

    world, ev, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None and _updated(ev).approvals == ("A",)  # 进度:仅 A 赞成
    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))
    assert err is None and _updated(ev).approvals == ("A", "B")
    assert not any(isinstance(e, Persist) for e in ev)  # 投票不落库、不动积分

    world, ev, err = run(world, VoteFreeEntry(origin="C", approve=True))  # 全票
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True and closed.waived == ("D",)
    assert _room(world).entry_vote is None
    assert _room(world).waive_entry_for == {"D"}
    assert _room(world).seats[3].points == 100  # 投票全程不动座位筹码

    # 下一手:D 免费正常入局(非盲位 → bet_amount=0),清 new_here,消费快照
    world, ev, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK))
    assert err is None
    h = _room(world).hand
    d = _by_seat(h, 3)
    assert d.bet_amount == 0 and d.points == 100  # 免费入局,不 post
    assert _room(world).seats[3].new_here is False
    assert _room(world).waive_entry_for == set()  # 快照已消费


# ── ①.13 一票否决:任一投票人 reject → 失败,候选回到「付盲即玩」常规 ──
def test_single_reject_fails_vote():
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None

    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=False))  # 一票否决
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is False and closed.waived == ()
    assert _room(world).entry_vote is None and _room(world).waive_entry_for == set()

    # 下一手:D 未被免 → 付盲即玩 post 一个 BB(非盲位)
    world, _, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK))
    assert err is None
    d = _by_seat(_room(world).hand, 3)
    assert d.bet_amount == BB and d.points == 98  # 付盲即玩(live)


# ── ①.14 投票后蹭车被挡:通过后才坐下的新玩家不在 waive_entry_for 快照里 ──
def test_late_joiner_not_in_waive_snapshot():
    world = _three_plus_newcomer()
    world = _add_watcher(world, "E")  # E 此刻观战、未就座(不是 new_here 候选)
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    for v in ("A", "B", "C"):
        world, ev, err = run(world, VoteFreeEntry(origin=v, approve=True))
        assert err is None
    assert _room(world).waive_entry_for == {"D"}  # 通过那刻快照 = {D}

    # E 现在才坐下成 new_here 座位 → 快照已定,E 不被追加(蹭车被挡)
    world, ev, err = run(world, SitDown(origin="E", seat=4))
    assert err is None and _room(world).seats[4].new_here is True  # E 确是新人候选
    assert _room(world).waive_entry_for == {"D"}  # 但快照恒为 {D},E 不在其中


# ── ①.15 投票人离场重算:开票后某投票人 LeaveRoom → 剩余投票人已全 approve → 通过 ──
def test_voter_leave_recomputes_and_passes():
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None
    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))  # A、B 赞成,C 未投
    assert err is None and _closed(ev) is None  # 尚未通过(C 还是投票人)

    world, ev, err = run(world, LeaveRoom(origin="C"))  # C 离场 → 投票人重算为 {A,B} → 全票
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True and closed.waived == ("D",)
    assert _room(world).entry_vote is None and _room(world).waive_entry_for == {"D"}
    assert "C" not in _room(world).users_in_room  # C 确已离场


# ── ①.15 变体:投票人坐出 → 投票人集合缩小重算 → 通过(_set_user_status 挂钩)──
def test_voter_sit_out_recomputes_and_passes():
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    for v in ("A", "B"):
        world, _, err = run(world, VoteFreeEntry(origin=v, approve=True))
        assert err is None

    world, ev, err = run(world, SetUserStatus(origin="C", status=UserStatus.SITTING_OUT))  # C 退出投票人集合
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True and closed.waived == ("D",)
    assert _room(world).entry_vote is None and _room(world).waive_entry_for == {"D"}


# ── ①.15 设计钉(0020 决策,0074 复核确认):投票人**掉线**不单独触发重算 ──
def test_voter_disconnect_does_not_trigger_vote():
    # rules.md ①.15 明写「不为断线单独触发通过」:断线**可逆**——占座窗口内可重连、重连后仍是
    # READY_TO_PLAY 投票人,此刻按「减员」结算等于剥夺其否决权(免盲是全票制);离场/坐出/起身
    # 才是主动且不可逆的退出,故只有它们触发。断线者真不回来时,Cleanup 走 _evict 自然重算。
    # 本测是**反向钉**:0074 曾把这条有意设计误当 bug「修」掉,此钉防再犯。
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    for v in ("A", "B"):
        world, _, err = run(world, VoteFreeEntry(origin=v, approve=True))
        assert err is None

    world, ev, err = run(world, Disconnect(origin=None, nick="C"))  # C 掉线(仅退出 voters,可重连)
    assert err is None
    assert _closed(ev) is None  # 不产 FreeEntryVoteClosed:不为断线单独结算
    assert _room(world).entry_vote is not None  # 投票仍挂着,等 C 重连投票 / 或 Cleanup 时重算
    assert _room(world).waive_entry_for == set()  # 未免盲
    assert _room(world).users_in_room["C"] is UserStatus.OFFLINE  # 掉线者保座


# ── 候选自身可发起投票(开票者不必是投票人,决策 5)──
def test_candidate_can_open_vote():
    world = _three_plus_newcomer()
    world, ev, err = run(world, OpenFreeEntryVote(origin="D"))  # 新人 D 自己请求免盲
    assert err is None and _updated(ev).candidates == ("D",)
    assert _room(world).entry_vote is not None


# ── 开票门槛:不在房 → NOT_IN_ROOM ──
def test_open_not_in_room():
    world = _three_plus_newcomer()
    world, ev, err = run(world, OpenFreeEntryVote(origin="Z"))  # Z 不在 world.users
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM
    assert ev == [] and _room(world).entry_vote is None


# ── 开票门槛:无 new_here 候选 → CANNOT_OPEN_VOTE ──
def test_open_no_candidates():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},  # 全已入局,无新人
        button=1,
    )
    world, ev, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is not None and err.code is ErrorCode.CANNOT_OPEN_VOTE
    assert _room(world).entry_vote is None


# ── 开票门槛:无合格投票人(全是新人)→ CANNOT_OPEN_VOTE(真空守门,不会误免)──
def test_open_no_voters():
    world = make_table(
        {0: seat("A", 100, new_here=True), 1: seat("B", 100, new_here=True)},  # 全新人 → 无投票人
        button=1,
    )
    world, ev, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is not None and err.code is ErrorCode.CANNOT_OPEN_VOTE
    assert _room(world).entry_vote is None and _room(world).waive_entry_for == set()


# ── 开票幂等:已有进行中投票时再开 → no-op,不重置已有 approvals ──
def test_reopen_is_idempotent_noop():
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None and _room(world).entry_vote.approvals == {"A"}

    world, ev, err = run(world, OpenFreeEntryVote(origin="B"))  # 再开
    assert err is None and ev == []  # 幂等 no-op
    assert _room(world).entry_vote.approvals == {"A"}  # 已有赞成未被重置


# ── 投票门槛:无进行中投票 → NO_VOTE_IN_PROGRESS ──
def test_vote_without_open():
    world = _three_plus_newcomer()
    world, ev, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is not None and err.code is ErrorCode.NO_VOTE_IN_PROGRESS
    assert ev == []


# ── 投票门槛:非合格投票人(new_here 候选自己)投票 → NOT_A_VOTER ──
def test_non_voter_cannot_vote():
    world = _three_plus_newcomer()
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, ev, err = run(world, VoteFreeEntry(origin="D", approve=True))  # D 是候选,非投票人
    assert err is not None and err.code is ErrorCode.NOT_A_VOTER
    assert _room(world).entry_vote.approvals == set()  # 非法投票不计入


# ── 候选冻结:原候选离场 → 孤儿票按失败清空,不被陈旧 approvals 复用免掉后来的新候选(防躲盲)──
def test_departed_candidate_orphan_vote_cleared_no_freeride():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("T", 100, new_here=True)},
        button=0,
    )
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))  # 候选冻结 = {T}
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))  # 仅 A 赞成(B 未表态)
    assert err is None

    world, ev, err = run(world, LeaveRoom(origin="T"))  # 唯一候选离场 → 票失对象
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is False and closed.waived == ()  # 失败清空(非 passed=True 空 waive)
    assert _room(world).entry_vote is None and _room(world).waive_entry_for == set()

    # 孤儿票已清:B 再投 → 无票可投(A 对 T 的旧赞成不会被复用去免任何后来的新候选)
    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))
    assert err is not None and err.code is ErrorCode.NO_VOTE_IN_PROGRESS
    assert _room(world).waive_entry_for == set()  # 谁都没被免


# ── 候选冻结:投票开启后中途坐下的新人不被并入 waive(approver 只对开票那批表态)──
def test_mid_vote_joiner_not_waived():
    world = _three_plus_newcomer()  # A/B/C 投票人 + D 候选
    world = _add_watcher(world, "F")  # F 观战,尚未就座
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))  # 候选冻结 = {D}
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="B", approve=True))
    assert err is None

    world, _, err = run(world, SitDown(origin="F", seat=4))  # F 中途就座成 new_here
    assert err is None and _room(world).seats[4].new_here is True

    world, ev, err = run(world, VoteFreeEntry(origin="C", approve=True))  # 全票
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True
    assert closed.waived == ("D",)  # 仅原候选 D;F 中途坐下不蹭车
    assert _room(world).waive_entry_for == {"D"}


# ── 非阻塞:投票未完即 StartHand → 不卡开局,候选按常规付盲,残票随开局作废(不跨手悬挂)──
def test_pending_vote_discarded_on_start_hand():
    world = _three_plus_newcomer()  # button=3 → D=UTG 非盲位
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))  # 未达全票
    assert err is None and _room(world).entry_vote is not None

    world, _, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK))
    assert err is None
    assert _room(world).entry_vote is None  # 残票作废,不跨手悬挂
    d = _by_seat(_room(world).hand, 3)
    assert d.bet_amount == BB and d.points == 98  # 未被免 → 付盲即玩(投票不卡开局)


# ── 进度广播:陈旧 approval(已离场投票人)被剔除(approvals & voters)──
def test_progress_prunes_departed_voter_approval():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("E", 100, new_here=False),  # 第 4 个投票人
            4: seat("D", 100, new_here=True),  # 候选
        },
        button=0,
    )
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))  # voters={A,B,C,E}
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="E", approve=True))  # E 赞成
    assert err is None

    world, _, err = run(world, LeaveRoom(origin="E"))  # E 离场(已赞成,但不再是投票人)
    assert err is None and _room(world).entry_vote is not None  # A 赞成、B/C 未投 → 未通过

    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))  # 触发进度广播
    assert err is None
    upd = _updated(ev)
    assert upd is not None and upd.approvals == ("A", "B")  # E 已离场 → 陈旧 approval 被剔除
    assert "E" not in upd.voters


# ── BUG-9(0088):重连拿到的 StateSnapshot 要带着进行中的投票,否则面板凭空消失、投票卡死 ──
def test_snapshot_projects_running_entry_vote():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True),  # 候选
        },
        button=0,
    )
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))  # 未达全票,票还在
    assert err is None and _room(world).entry_vote is not None

    # B 掉线再重连:重连臂私发 StateSnapshot —— 它必须带着这张还在等 B 表态的票
    world, _, err = run(world, Disconnect(origin=None, nick="B"))
    assert err is None
    world, events, err = run(world, Connect(origin=None, nick="B"))
    assert err is None
    snap = next(e.msg for e in events if isinstance(e, Personal) and isinstance(e.msg, StateSnapshot))
    assert snap.free_entry_vote is not None
    assert snap.free_entry_vote.candidates == ("D",)
    assert snap.free_entry_vote.approvals == ("A",)
    # 此刻 B 还不是投票人:重连恢复到 SITTING_IN,不是 READY_TO_PLAY(见 _restore_status)。
    assert "B" not in snap.free_entry_vote.voters

    # 他再点一次 Ready 才重新成为合格投票人 —— 而这件事必须有事件说出来,否则他的面板永远显示
    # 「你不是本次的投票人」,全票制下这张票就此卡死(0088 补的另一半)。
    world, events, err = run(world, SetUserStatus(origin="B", status=UserStatus.READY_TO_PLAY, seat=1))
    assert err is None
    upd = _updated(events)
    assert upd is not None and "B" in upd.voters and upd.approvals == ("A",)


def test_snapshot_has_no_vote_when_none_running():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=0,
    )
    world, events, err = run(world, Connect(origin=None, nick="B"))
    assert err is None
    snap = next(e.msg for e in events if isinstance(e, Personal) and isinstance(e.msg, StateSnapshot))
    assert snap.free_entry_vote is None


# ── 多候选:waive 快照按 nick 排序产出 ──
def test_multi_candidate_waive_sorted():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("D", 100, new_here=True),  # 候选
            3: seat("C", 100, new_here=True),  # 候选(故意 nick 序在 D 之后)
        },
        button=0,
    )
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))  # 候选冻结 = {C,D}
    assert err is None
    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None
    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))  # 全票
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True and closed.waived == ("C", "D")  # 排序产出
    assert _room(world).waive_entry_for == {"C", "D"}


# ── 投票人界定:已入局但 SITTING_OUT 者不是投票人(无需其表态即可通过)──
def test_sitting_out_established_not_a_voter():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("E", 100, new_here=False),  # 已入局但坐出 → 非投票人
            3: seat("D", 100, new_here=True),  # 候选
        },
        button=0,
        statuses={"E": UserStatus.SITTING_OUT},
    )
    world, ev, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None and _updated(ev).voters == ("A", "B")  # E 坐出 → 不在投票人集

    world, _, err = run(world, VoteFreeEntry(origin="A", approve=True))
    assert err is None
    world, ev, err = run(world, VoteFreeEntry(origin="B", approve=True))  # A/B 全票即可,无需 E
    assert err is None
    closed = _closed(ev)
    assert closed is not None and closed.passed is True and closed.waived == ("D",)
