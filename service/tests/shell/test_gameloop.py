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


# ── 兜底范围(changes/0083 / BUG-7):唯一状态写者不得因单条命令而退出 ──

def test_dispatch_exception_neither_escapes_nor_strands_the_rest(monkeypatch, caplog):
    # commit 成功之后某个事件的派发崩了。两条要求:
    #   ① 不得冒出去 —— 此前 `except` 只裹 reduce() 一行,这里的异常会一路杀掉唯一状态写者协程;
    #   ② 不得连累同批其余事件 —— 丢一条 Persist = 手牌记录永久丢失,丢一条 TurnChanged = Timer 不装表、
    #      该行动的人能无限拖住整桌。
    # 且**不回 INTERNAL**:commit 已生效,回它等于骗客户端「什么都没发生」,还会诱导重试重复生效
    # (error.md 定义 INTERNAL 是「工作副本已丢、world 未动」)。改落 CRITICAL 留人工介入。
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice", "bob")
    real = sh.dispatcher.dispatch
    seen = []

    def first_one_explodes(ev):
        seen.append(ev)
        if len(seen) == 1:
            raise RuntimeError("dispatch kaboom")
        real(ev)

    monkeypatch.setattr(sh.dispatcher, "dispatch", first_one_explodes)
    with caplog.at_level(logging.CRITICAL):
        sh.gameloop.handle(SitDown(origin="alice", seat=0))  # 不得抛
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN  # commit 已生效
    assert not any(isinstance(m, ErrorMessage) for m in drain(conns["alice"]))  # 没有骗人的 INTERNAL
    assert not any(r.levelno == logging.CRITICAL for r in caplog.records)  # 逐事件兜住了,没升级成半途态


def test_post_commit_crash_logs_critical_instead_of_lying_to_client(monkeypatch, caplog):
    # 崩在 commit 之后、且不在逐事件兜底射程内(这里用审计):world 已经改了 → 落 CRITICAL,不回 INTERNAL。
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice")

    def boom(events):
        raise RuntimeError("audit kaboom")

    monkeypatch.setattr(sh.gameloop, "_audit_applied", boom)
    with caplog.at_level(logging.CRITICAL):
        sh.gameloop.handle(SitDown(origin="alice", seat=0))  # 不得抛
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN  # commit 已生效
    assert drain(conns["alice"]) == []  # 不回 INTERNAL(那会说成「world 未动」)
    assert any(r.levelno == logging.CRITICAL and "world committed" in r.getMessage() for r in caplog.records)


def test_checkout_exception_does_not_escape_handle(monkeypatch):
    # checkout(工作副本深拷贝)崩:同样在旧 except 的射程之外。
    world = _two_watchers()
    sh = Shell(world)
    conns = sh.connect("alice")

    def boom(world_, cmd):
        raise RuntimeError("checkout kaboom")

    monkeypatch.setattr(gameloop_mod.world_api, "checkout", boom)
    sh.gameloop.handle(SitDown(origin="alice", seat=0))  # 不得抛
    assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING  # commit 之前崩 ⇒ world 一字节未动
    a = drain(conns["alice"])
    assert len(a) == 1 and isinstance(a[0], ErrorMessage) and a[0].code.value == "INTERNAL"


async def test_run_survives_crashing_command_and_keeps_processing(monkeypatch):
    # architecture.md「接住 → 继续处理下一条」:第一条命令的 dispatch 崩,GameLoop 仍在,第二条照常处理。
    import asyncio

    world = _two_watchers()
    sh = Shell(world)
    sh.connect("alice", "bob")
    real = sh.dispatcher.dispatch
    calls = {"n": 0}

    def flaky(ev):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first dispatch kaboom")
        real(ev)

    monkeypatch.setattr(sh.dispatcher, "dispatch", flaky)
    gl = asyncio.create_task(sh.gameloop.run())
    try:
        sh.inbox.put_nowait(SitDown(origin="alice", seat=0))
        sh.inbox.put_nowait(SitDown(origin="bob", seat=1))
        await asyncio.wait_for(sh.inbox.join(), timeout=1.0)
        assert not gl.done()  # 唯一状态写者还活着
        assert world.rooms["r1"].seats[1] is not None  # 第二条命令照常处理
    finally:
        gl.cancel()
        try:
            await gl
        except asyncio.CancelledError:
            pass
