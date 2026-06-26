# 连接登记/路由/顶替(见 connection.md)。连接绑 nick、不绑房间(模型 2);
# 明文 dev 版:无 SecureChannel,outbound 装明文 ServerMessage(Sender 序列化),加密留 P5。

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app import gameconfig
from app.shell.ratelimit import TokenBucket


@dataclass
class Connection:
    nick: str  # 会话身份;一个 nick 全局一条有效连接
    session_id: str  # 会话句柄(dev 用 nick / 随机),审计/日志关联
    ws: Any  # 物理 ws(FastAPI WebSocket 或测试 fake);明文 dev 无 SecureChannel
    outbound: "asyncio.Queue[Any]"  # 有界;装明文 ServerMessage,满 = 慢客户端(见 dispatch._enqueue)
    sender_task: asyncio.Task | None = None  # 本连接 Sender 协程句柄;起 Sender 前为 None,退出/顶替时 cancel
    chat_bucket: TokenBucket | None = None  # 房聊发件人维度令牌桶(每连接,见 messaging.md / ratelimit);create 时建满桶
    # 注:用户在哪个房间是 world.users[nick].room,不是连接字段(连接绑 nick、不绑房)。

    @classmethod
    def create(cls, nick: str, session_id: str, ws: Any) -> "Connection":
        return cls(
            nick=nick,
            session_id=session_id,
            ws=ws,
            outbound=asyncio.Queue(maxsize=gameconfig.OUTBOUND_MAX),
            chat_bucket=TokenBucket.create(
                gameconfig.ROOM_CHAT_RATE_BURST, gameconfig.ROOM_CHAT_RATE_PER_SEC, time.monotonic()
            ),
        )


class ConnectionManager:
    def __init__(self) -> None:
        self._by_nick: dict[str, Connection] = {}  # nick → Connection(全局,房间无关)

    def register(self, conn: Connection) -> Connection | None:
        old = self._by_nick.get(conn.nick)  # 同 nick 旧连接 = 被顶替(调用方关旧 ws + cancel 旧 Sender)
        self._by_nick[conn.nick] = conn
        return old

    def unregister(self, conn: Connection) -> None:
        if self._by_nick.get(conn.nick) is conn:  # 仅删自己:顶替后旧连接退出不误删已上位的新连接
            del self._by_nick[conn.nick]

    def is_current(self, conn: Connection) -> bool:
        return self._by_nick.get(conn.nick) is conn  # 退出时判「我还是当前连接吗」(决定是否投 Disconnect)

    def get(self, nick: str) -> Connection | None:
        return self._by_nick.get(nick)  # Personal / 广播成员 / 错误回发,全按 nick O(1)

    def online_nicks(self) -> set[str]:
        return set(self._by_nick)  # 全体有 live 连接的 nick(新拷贝,调用方可改;presence「在线」源,见 presence.md)

    def rename(self, old: str, new: str) -> None:
        # 改昵称(仅大厅,见 presence.md):连接从 old 键重挂到 new 键 + 改 Connection.nick,
        # 否则私聊/路由按新 nick 找不到旧连接。无 old 连接(未连接时改名只改库)→ no-op。
        # 前提:调用方(P7 改昵称 REST)须先过 DB nickname 唯一约束 + 仅大厅判定,故 new 键不会撞活连接。
        conn = self._by_nick.pop(old, None)
        if conn is not None:
            conn.nick = new
            self._by_nick[new] = conn
