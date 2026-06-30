# GameLoop:唯一状态写者(见 architecture.md「统一回滚」)。取命令 → 工作副本 reduce →
# 成功 commit + dispatch、失败/异常丢工作副本 + 回发发起人。处理一条命令期间不 await(派发只 put_nowait)。

import asyncio
import logging

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


class GameLoop:
    def __init__(self, world: World, inbox: "asyncio.Queue[Command]", dispatcher: Dispatcher) -> None:
        self.world = world
        self.inbox = inbox
        self.dispatcher = dispatcher

    async def run(self) -> None:
        while True:
            cmd = await self.inbox.get()  # 唯一让出点
            self.handle(cmd)

    def handle(self, cmd: Command) -> None:
        # 一条命令的处理(同步,抽出供测试 / 关闭排空(lifespan.stop ②)直接驱动):checkout → reduce → commit/discard → dispatch。
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
            self._audit_applied(events)
            for ev in events:
                self.dispatcher.dispatch(ev)  # 只 put_nowait / 调本地快设施
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
