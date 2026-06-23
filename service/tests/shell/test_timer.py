"""Timer:两表维护 + tick 触发 + epoch staleness 元数据(timer.md)。
用 monkeypatch 控制单调时钟,确定性驱动 tick(不睡眠)。"""

import asyncio

from app.core.commands import Cleanup, Timeout
from app.shell import timer as timer_mod
from app.shell.timer import Timer


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _timer_with_clock(monkeypatch) -> tuple[Timer, _Clock, "asyncio.Queue"]:
    clock = _Clock()
    monkeypatch.setattr(timer_mod, "now", clock)
    inbox: "asyncio.Queue" = asyncio.Queue()
    return Timer(inbox), clock, inbox


def test_action_timeout_fires_with_epoch(monkeypatch):
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.on_turn_changed("r1", "alice", epoch=7, timeout_s=15)
    clock.t += 10
    t.tick()
    assert inbox.empty()  # 未到期:不触发
    clock.t += 10  # 共 +20 > 15
    t.tick()
    cmd = inbox.get_nowait()
    assert isinstance(cmd, Timeout) and cmd.nick == "alice" and cmd.epoch == 7 and cmd.origin is None
    t.tick()
    assert inbox.empty()  # 一次性:触发即删,不重复投


def test_on_turn_changed_same_room_overwrites(monkeypatch):
    # 同房覆盖 = 取消上一回合:新回合 epoch 生效,旧 deadline 不再触发。
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.on_turn_changed("r1", "alice", epoch=1, timeout_s=15)
    t.on_turn_changed("r1", "bob", epoch=2, timeout_s=15)
    clock.t += 20
    t.tick()
    cmd = inbox.get_nowait()
    assert cmd.nick == "bob" and cmd.epoch == 2
    assert inbox.empty()  # 只一条(旧的被覆盖)


def test_clear_action_cancels(monkeypatch):
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.on_turn_changed("r1", "alice", epoch=1, timeout_s=15)
    t.clear_action("r1")
    clock.t += 100
    t.tick()
    assert inbox.empty()  # 已清:不触发


def test_liveness_fires_cleanup_and_heartbeat_extends(monkeypatch):
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.heartbeat("alice")  # fire_at = 1000 + LIVENESS_TIMEOUT(90)
    clock.t += 50
    t.heartbeat("alice")  # 续命:fire_at = 1050 + 90 = 1140
    clock.t += 50  # t=1100 < 1140
    t.tick()
    assert inbox.empty()  # 续命后未到期
    clock.t += 50  # t=1150 > 1140
    t.tick()
    cmd = inbox.get_nowait()
    assert isinstance(cmd, Cleanup) and cmd.nick == "alice" and cmd.origin is None


def test_drop_liveness_removes(monkeypatch):
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.heartbeat("alice")
    t.drop_liveness("alice")
    clock.t += 1000
    t.tick()
    assert inbox.empty()
