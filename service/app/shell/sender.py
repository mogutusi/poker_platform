# Sender:per-connection 出站协程。从 outbound 取明文 ServerMessage → ws 发 JSON,严格保序
# (单任务消费单队列即天然保序)。隔离慢客户端:慢的卡在自己的 ws.send,不拖累 GameLoop(见 architecture.md)。
# 明文 dev 版:直接 model_dump_json;P5 国密落地时在此加密成帧(outbound 仍装明文)。

import asyncio
import logging

from app.shell.connection import Connection

log = logging.getLogger(__name__)


async def sender_loop(conn: Connection) -> None:
    try:
        while True:
            msg = await conn.outbound.get()  # 让出点:队列空则等
            await conn.ws.send_text(msg.model_dump_json())  # 让出点:慢客户端卡这里(只影响本连接)
    except asyncio.CancelledError:
        raise  # 顶替/关闭时被 cancel:正常退出路径
    except Exception:
        log.info("sender exit nick=%s (ws closed?)", conn.nick)  # ws 断/异常:退出,清理由 Receiver finally 兜
