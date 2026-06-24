"""GameLoop:工作副本 commit-or-discard + dispatch 路由 + 错误回发 + 异常归一(architecture.md)
+ 命令进→事件出边界审计 + 脱敏红线(log.md)。只验接线(core 规则已在 tests/core 覆盖)。"""

import logging
from datetime import datetime, timezone

from app.core.commands import BuyIn, SitDown, StartHand
from app.core.enums import UserStatus
from app.core.domain import UserState
from app.shell import gameloop as gameloop_mod
from app.shell.logsetup import _ContextFilter
from app.wire.server import ErrorMessage, UserStatusChanged
from tests.builders import DECK, make_table, make_world, room_with, seat
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


# ── 边界审计(log.md):命令进→事件出在 GameLoop.handle 一处可见 ──
def test_boundary_audit_logs_received_and_applied_with_context(caplog):
    world = _two_watchers()
    sh = Shell(world)
    sh.connect("alice", "bob")
    caplog.handler.addFilter(_ContextFilter())  # 让 caplog 也拍上 handle 内绑定的关联字段
    with caplog.at_level(logging.DEBUG, logger="app.shell.gameloop"):
        sh.gameloop.handle(SitDown(origin="alice", seat=0))
    received = next(r for r in caplog.records if "cmd received" in r.getMessage())
    assert received.cmd_type == "SitDown" and received.nick == "alice" and received.room == "r1"  # 关联字段绑定
    assert any("cmd applied" in r.getMessage() for r in caplog.records)  # 事件摘要


def test_boundary_audit_business_error_is_warning_not_error(caplog):
    world = _two_watchers()
    sh = Shell(world)
    sh.connect("alice")
    with caplog.at_level(logging.DEBUG, logger="app.shell.gameloop"):
        sh.gameloop.handle(SitDown(origin="alice", seat=99))  # 越界 → Err(NOT_YOUR_SEAT)
    rejected = [r for r in caplog.records if "rejected" in r.getMessage()]
    assert rejected and rejected[0].levelno == logging.WARNING  # 预期内失败 = WARNING,非 ERROR
    assert "NOT_YOUR_SEAT" in rejected[0].getMessage()


def test_boundary_audit_reduce_exception_is_error_with_traceback(caplog, monkeypatch):
    world = _two_watchers()
    sh = Shell(world)
    sh.connect("alice")
    monkeypatch.setattr(gameloop_mod, "reduce", lambda work, cmd: (_ for _ in ()).throw(RuntimeError("kaboom")))
    with caplog.at_level(logging.DEBUG, logger="app.shell.gameloop"):
        sh.gameloop.handle(SitDown(origin="alice", seat=0))
    crashed = [r for r in caplog.records if "reduce crashed" in r.getMessage()]
    assert crashed and crashed[0].levelno == logging.ERROR and crashed[0].exc_info is not None  # ERROR + traceback


# ── 脱敏红线(log.md):即便事件携带底牌(HoleCards/HandStarted),审计也只记类型计数,绝不泄露牌面 ──
def test_audit_never_logs_hole_cards_or_deck(caplog):
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0)
    sh = Shell(world)
    sh.connect("A", "B")
    with caplog.at_level(logging.DEBUG):  # root,全量
        sh.gameloop.handle(
            StartHand(origin="A", seat=0, started_at=datetime(2026, 1, 1, tzinfo=timezone.utc), deck=DECK)
        )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "cmd applied" in text and "hand milestone: hand_started" in text  # 审计确实跑了(非空过)
    # 取**真正发出的**底牌(从已提交的 hand 读,而非重算公式)→ 发牌逻辑/座位数变了断言自适应,不会假绿
    hand = world.rooms["r1"].hand
    assert hand is not None and len(hand.players) == 2  # 前提:手真开起来了
    for p in hand.players:
        for c in p.hole_cards:
            code = c.rank.value + c.suit.value
            assert code not in text, f"hole card {code} leaked into logs"
    assert "deck" not in text.lower() and "牌堆" not in text  # 余牌也不入日志(英文 deck + 中文牌堆 detail)
