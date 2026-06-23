# Timer:shell 协程,两张「到期时刻」表 → 周期扫描 → 过期项变 Command 投 inbox(见 timer.md)。
# 绝不直接改 world、绝不 ws.send;决策时刻只活在 shell(单调时钟,不读墙钟);取消=隐式(staleness 由 reduce 兜)。

import asyncio
import logging
import time
from dataclasses import dataclass

from app import gameconfig
from app.core.commands import Cleanup, Command, Timeout

log = logging.getLogger(__name__)


def now() -> float:
    return time.monotonic()  # 单调时钟(timer.md 许可;不读墙钟免 NTP 误触,且无需运行中事件循环)


@dataclass
class _ActionDeadline:
    nick: str  # 行动者
    epoch: int  # 回合新鲜度判据(= hand.epoch);Timeout 带回,reduce 进门比对挡过期
    fire_at: float  # 到期单调时刻


class Timer:
    def __init__(self, inbox: "asyncio.Queue[Command]") -> None:
        self._inbox = inbox
        self._action: dict[str, _ActionDeadline] = {}  # room → 当前行动倒计时(每房至多一人行动)
        self._liveness: dict[str, float] = {}  # nick → 保活到期时刻(按 nick 单键:Receiver 只知 nick)

    # ── 游戏层:GameLoop.dispatch 调(reduce 产 TurnChanged / ClearAction)──
    def on_turn_changed(self, room: str, nick: str, epoch: int, timeout_s: float | None = None) -> None:
        s = gameconfig.ACTION_TIMEOUT if timeout_s is None else timeout_s
        self._action[room] = _ActionDeadline(nick, epoch, now() + s)  # 同房覆盖 = 取消上一回合

    def clear_action(self, room: str) -> None:
        self._action.pop(room, None)  # 手结束:停该房行动倒计时

    # ── 连接层:Receiver 调(只知 nick、不读 world)──
    def heartbeat(self, nick: str) -> None:
        self._liveness[nick] = now() + gameconfig.LIVENESS_TIMEOUT  # 收到任意帧续命

    def drop_liveness(self, nick: str) -> None:
        self._liveness.pop(nick, None)

    # ── 扫描 ──
    async def run(self) -> None:
        tick = gameconfig.TIMER_TICK_MS / 1000  # 唯一让出点
        while True:
            await asyncio.sleep(tick)
            self.tick()

    def tick(self) -> None:
        # 扫两表,过期项投 inbox(一次性,触发即删)。抽成同步方法,供测试不睡眠直接驱动。
        t = now()
        for room, d in list(self._action.items()):
            if t >= d.fire_at:
                self._fire(Timeout(origin=None, nick=d.nick, epoch=d.epoch))  # 不带 room,reduce 解析
                del self._action[room]
        for nick, fire_at in list(self._liveness.items()):
            if t >= fire_at:
                self._fire(Cleanup(origin=None, nick=nick))  # 不带 room,reduce 解析
                del self._liveness[nick]

    def _fire(self, cmd: Command) -> None:
        # inbox 满 = GameLoop 卡死(architecture.md「inbox 满」CRITICAL):丢该命令 + 落 CRITICAL,
        # 但**不让 Timer 协程崩**——否则后续所有超时/清理静默失效。
        try:
            self._inbox.put_nowait(cmd)
        except asyncio.QueueFull:
            log.critical("inbox full; dropping timer command %s", type(cmd).__name__)
