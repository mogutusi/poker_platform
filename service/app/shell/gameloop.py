# GameLoop:唯一状态写者(见 architecture.md「统一回滚」)。取命令 → 工作副本 reduce →
# 成功 commit + dispatch、失败/异常丢工作副本 + 回发发起人。处理一条命令期间不 await(派发只 put_nowait)。

import asyncio
import logging
from dataclasses import dataclass

from app.core.commands import Command
from app.core.domain import World
from app.core.errors import Err, ErrorCode
from app.core.events import Broadcast, Event
from app.core.reduce import reduce
from app.shell import world as world_api
from app.shell.dispatch import Dispatcher
from app.shell.logsetup import bind_log_context, reset_log_context
from app.wire.server import HandEnded, HandStarted

log = logging.getLogger(__name__)


def _event_summary(events: list[Event]) -> str:
    # 事件摘要 = 仅类型计数(脱敏:绝不序列化 payload,见 log.md);如 "Broadcast=2 Personal=1 Persist=1"。
    counts: dict[str, int] = {}
    for ev in events:
        counts[type(ev).__name__] = counts.get(type(ev).__name__, 0) + 1
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"


@dataclass
class _Progress:
    committed: bool = False  # 本条命令的 commit 是否已发生;决定崩溃后能不能对客户端说「world 未动」


class GameLoop:
    def __init__(self, world: World, inbox: "asyncio.Queue[Command]", dispatcher: Dispatcher) -> None:
        self.world = world
        self.inbox = inbox
        self.dispatcher = dispatcher

    async def run(self) -> None:
        while True:
            cmd = await self.inbox.get()  # 唯一让出点
            try:
                self.handle(cmd)
            finally:
                self.inbox.task_done()  # 供 inbox.join() 屏障(0073):handle 异常也计数,免 join 悬死

    def handle(self, cmd: Command) -> None:
        # 一条命令的处理(同步,抽出供测试 / 关闭排空(lifespan.stop ②)直接驱动)。
        # 兜底范围 = 整条链(0083 / BUG-7):此前 `except` 只裹 `reduce()` 一行,而 checkout(深拷贝)、commit、
        # 审计、派发抛出的异常会一路冒出 `run()`、杀掉唯一状态写者协程且无人察觉——与 architecture.md
        # 「接住 → 继续处理下一条」的承诺不符。
        # **commit 前后两种崩法必须分开处置**(否则会骗客户端):
        #   崩在 commit 之前 → 工作副本被丢、`world` 一字节未动,正是 error.md 定义的 INTERNAL,照回;
        #   崩在 commit 之后 → `world` 已经改了,再回 INTERNAL 等于告诉客户端「什么都没发生」,还会诱导它
        #     重试而重复生效。这是进程级异常态:落 CRITICAL 留人工介入,客户端的真相以后续 StateSnapshot 为准。
        progress = _Progress()  # 异常逃逸时拿不到返回值,只能靠这个可变标记把「commit 是否已发生」带出来
        try:
            self._handle(cmd, progress)
        except Exception:
            # 关联字段显式写进消息:本兜底在 `bind_log_context` 之外(checkout 也要罩住),
            # contextvar 此刻可能已复原或根本没绑上,只靠 filter 会打出一条 `cmd_type=<MISSING>` 的瞎日志。
            if progress.committed:
                log.critical(
                    "world committed but side effects incomplete cmd_type=%s nick=%s",
                    type(cmd).__name__, cmd.origin, exc_info=True,
                )
                return
            log.exception(
                "command handling crashed before commit cmd_type=%s nick=%s", type(cmd).__name__, cmd.origin
            )
            try:
                self.dispatcher.send_error(cmd, Err(ErrorCode.INTERNAL, "command handling internal error"))
            except Exception:  # 回发本身再崩:吞掉,绝不让兜底自己杀掉 GameLoop
                log.exception("failed to report internal error to origin")

    def _handle(self, cmd: Command, progress: "_Progress") -> None:
        # checkout → reduce → commit/discard → dispatch。
        # 日志挂此边界(log.md):命令进→事件出全程可见,无需在 reduce 分支里插 log(core 零日志,守不变量 1)。
        work = world_api.checkout(self.world, cmd)  # ① 解析目标房 + 深拷贝(房 + users 表)
        hand = work.room.hand if work.room is not None else None
        token = bind_log_context(  # 关联字段:本命令处理期间所有 GameLoop 日志自动带上(handle 全程无 await)
            cmd_type=type(cmd).__name__,
            nick=cmd.origin,
            room=work.room_name,
            hand_seq=hand.seq if hand is not None else None,
            hand_epoch=hand.epoch if hand is not None else None,
        )
        try:
            log.debug("cmd received")
            try:
                events, err = reduce(work, cmd)  # ② 同步,只改副本
            except Exception:  # 未预期 bug:丢工作副本,归一为 INTERNAL 回发(architecture.md 错误处理)
                log.exception("reduce crashed")  # ERROR + traceback;工作副本已丢、world 未动
                self.dispatcher.send_error(cmd, Err(ErrorCode.INTERNAL, "reduce internal error"))
                return
            if err is not None:  # ③ 业务失败:不 commit,world 一字节未动
                log.warning("cmd rejected: code=%s detail=%s", err.code.value, err.detail)  # 预期内 → WARNING 非 ERROR
                self.dispatcher.send_error(cmd, err)
                return
            world_api.commit(self.world, work)  # ④ 成功:装回引用
            progress.committed = True
            self._audit_applied(events)
            for ev in events:
                try:
                    self.dispatcher.dispatch(ev)  # 只 put_nowait / 调本地快设施
                except Exception:
                    # 逐事件兜(0083):一个事件炸了不能连累同批其余事件。丢一条 `Persist` = 手牌记录永久丢失
                    # (写缓冲里只有 put 进去的,没进去的没人重试);丢一条 `TurnChanged` = Timer 不装表,
                    # 该行动的人可以无限拖住整桌(行动倒计时是唯一的兜底)。
                    log.exception("dispatch failed for %s", type(ev).__name__)
        finally:
            reset_log_context(token)  # 复原关联字段,不跨命令泄漏

    def _audit_applied(self, events: list[Event]) -> None:
        # 提交后审计:手牌里程碑 INFO(只记 type 字面量,无 payload)+ 事件类型计数 DEBUG。
        if log.isEnabledFor(logging.INFO):
            for ev in events:
                if isinstance(ev, Broadcast) and isinstance(ev.msg, (HandStarted, HandEnded)):
                    log.info("hand milestone: %s", ev.msg.type)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("cmd applied: %s", _event_summary(events))
