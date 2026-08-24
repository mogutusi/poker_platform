# Sender:per-connection 出站协程。从 outbound 取明文 ServerMessage → ws 发,严格保序
# (单任务消费单队列即天然保序)。隔离慢客户端:慢的卡在自己的 ws.send,不拖累 GameLoop(见 architecture.md)。
# outbound 始终装明文 ServerMessage(dispatch/core 不知有加密);加密在此边界:channel 非 None → seal 成二进制帧
# 发 send_bytes(iv‖ct‖mac,见 auth/channel.py);channel None(dev)→ 明文 JSON send_text(见 changes/0061)。

import asyncio
import logging
import time

from app.shell.connection import WS_CLOSE_UNAUTHENTICATED, Connection

log = logging.getLogger(__name__)


async def sender_loop(conn: Connection) -> None:
    try:
        while True:
            msg = await conn.outbound.get()  # 让出点:队列空则等
            if conn.session is not None and time.time() >= conn.session.expires_at:
                # 会话 exp 兜底的出站半边(0070):不给过期密钥再封任何密文——收帧侧检查覆盖不了
                # 「只收不发的过期连接」,此处补上;关连接后 Receiver 的 receive 报错 → finally 清理。
                log.warning("sender closing nick=%s reason=session_expired", conn.nick)
                try:
                    await conn.ws.close(code=WS_CLOSE_UNAUTHENTICATED)  # 同握手拒码:须重新登录换会话
                except Exception:
                    pass
                return
            payload = msg.model_dump_json()  # 明文 JSON(隐私由结构缺位保证,见 wire.md)
            if conn.channel is None:
                # 生产走不到这里(0086 退役明文端点后,唯一入口必带 channel);留作测试接缝。
                await conn.ws.send_text(payload)  # 让出点:慢客户端卡这里(只影响本连接)
            else:
                frame = conn.channel.seal(payload.encode("utf-8"))  # 加密成帧:seq 藏 ct、encrypt-then-MAC
                await conn.ws.send_bytes(frame)  # 让出点:慢客户端卡这里(只影响本连接)
    except asyncio.CancelledError:
        raise  # 顶替/关闭时被 cancel:正常退出路径
    except Exception:
        log.info("sender exit nick=%s (ws closed?)", conn.nick)  # ws 断/异常:退出,清理由 Receiver finally 兜
