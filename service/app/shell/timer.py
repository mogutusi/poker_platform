# Timer:shell 协程,两张「到期时刻」表 → 周期扫描 → 过期项变 Command 投 inbox(见 timer.md)。
# 行动倒计时按 room 键(reduce 经 TurnChanged 驱动);断线占座窗口按 nick 键(断线装表/重连拆表,0070)。
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
    hand_seq: int  # 这一手的房内单调号(= hand.seq);与 room/epoch 一起构成 Timeout 的身份(0090)
    epoch: int  # 回合新鲜度判据(= hand.epoch);Timeout 带回,reduce 进门比对挡过期
    fire_at: float  # 到期单调时刻


class Timer:
    def __init__(self, inbox: "asyncio.Queue[Command]") -> None:
        self._inbox = inbox
        self._action: dict[str, _ActionDeadline] = {}  # room → 当前行动倒计时(每房至多一人行动)
        self._liveness: dict[str, float] = {}  # nick → **断线占座窗口**到期时刻(条目只在离线期存在,0070)

    # ── 游戏层:GameLoop.dispatch 调(reduce 产 TurnChanged / ClearAction)──
    def on_turn_changed(
        self, room: str, nick: str, hand_seq: int, epoch: int, timeout_s: float | None = None
    ) -> None:
        s = gameconfig.ACTION_TIMEOUT if timeout_s is None else timeout_s
        self._action[room] = _ActionDeadline(nick, hand_seq, epoch, now() + s)  # 同房覆盖 = 取消上一回合

    def clear_action(self, room: str) -> None:
        self._action.pop(room, None)  # 手结束:停该房行动倒计时

    # ── 连接层:断线装表、重连拆表(0070 重设计;凡投 Disconnect 处必 arm)──
    # 掉线检测不在这里:传输层(uvicorn 默认 20s 协议 ping)负责发现死连接并令 Receiver 退出;
    # 本表只回答「已断线的人,座位再留多久」。在线用户不进表 ⇒ 无空触发、无「触发即删后断线漏清」坑。
    def arm_cleanup(self, nick: str) -> None:
        self._liveness[nick] = now() + gameconfig.LIVENESS_TIMEOUT  # 断线时刻起算占座窗口

    def cancel_cleanup(self, nick: str) -> None:
        self._liveness.pop(nick, None)  # 窗口内重连/顶替:拆表;竞态漏拆由 reduce 的 OFFLINE staleness 兜底

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
                # 带齐身份 (room, hand_seq, epoch):**room 不是路由字段**,reduce 照旧按 nick 解析目标房,
                # 它只用来挡「人已换房」的陈旧命令(见 timer.md 过期防护 / changes/0090)。
                self._fire(Timeout(origin=None, nick=d.nick, room=room, hand_seq=d.hand_seq, epoch=d.epoch))
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
