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


def test_arm_cleanup_fires_after_window_and_rearm_overwrites(monkeypatch):
    # 断线装表(0070):arm 后满窗触发 Cleanup;重复 arm(再次断线)覆盖旧到期时刻;一次性触发后表干净。
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.arm_cleanup("alice")  # fire_at = 1000 + LIVENESS_TIMEOUT(90)
    clock.t += 50
    t.arm_cleanup("alice")  # 重复断线:fire_at = 1050 + 90 = 1140
    clock.t += 50  # t=1100 < 1140
    t.tick()
    assert inbox.empty()  # 覆盖后未到期
    clock.t += 50  # t=1150 > 1140
    t.tick()
    cmd = inbox.get_nowait()
    assert isinstance(cmd, Cleanup) and cmd.nick == "alice" and cmd.origin is None
    clock.t += 1000
    t.tick()
    assert inbox.empty()  # 触发即删,不重复投


def test_cancel_cleanup_removes(monkeypatch):
    # 重连拆表(0070):窗口内重连取消断线倒计时,满窗不触发;未知 nick 幂等无害。
    t, clock, inbox = _timer_with_clock(monkeypatch)
    t.arm_cleanup("alice")
    t.cancel_cleanup("alice")
    t.cancel_cleanup("nobody")  # 在线用户不进表(0070):cancel 未知 nick 无害
    clock.t += 1000
    t.tick()
    assert inbox.empty()
