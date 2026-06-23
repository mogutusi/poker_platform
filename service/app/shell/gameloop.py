# GameLoop:唯一状态写者(见 architecture.md「统一回滚」)。取命令 → 工作副本 reduce →
# 成功 commit + dispatch、失败/异常丢工作副本 + 回发发起人。处理一条命令期间不 await(派发只 put_nowait)。

import asyncio
import logging

from app.core.commands import Command
from app.core.domain import World
from app.core.errors import Err, ErrorCode
from app.core.reduce import reduce
from app.shell import world as world_api
from app.shell.dispatch import Dispatcher

log = logging.getLogger(__name__)


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
        # 一条命令的处理(同步,抽出供测试直接驱动):checkout → reduce → commit/discard → dispatch。
        work = world_api.checkout(self.world, cmd)  # ① 解析目标房 + 深拷贝(房 + users 表)
        try:
            events, err = reduce(work, cmd)  # ② 同步,只改副本
        except Exception:  # 未预期 bug:丢工作副本,归一为 INTERNAL 回发(architecture.md 错误处理)
            log.exception("reduce crashed on %s", type(cmd).__name__)
            self.dispatcher.send_error(cmd, Err(ErrorCode.INTERNAL, "reduce internal error"))
            return
        if err is not None:  # ③ 业务失败:不 commit,world 一字节未动
            self.dispatcher.send_error(cmd, err)
            return
        world_api.commit(self.world, work)  # ④ 成功:装回引用
        for ev in events:
            self.dispatcher.dispatch(ev)  # 只 put_nowait / 调本地快设施
