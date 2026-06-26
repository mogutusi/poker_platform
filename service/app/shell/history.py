# 房聊内存环形缓冲(见 messaging.md §持久化):shell 私有连接态,每房最近 N 条,不进 world、不落库。
# 写者 = dispatch(GameLoop 协程,派发 ChatMessage 广播时 append);读者 = Receiver(自协程,FetchRoomChat 时 recent)。
# 单线程 asyncio 下两端皆无 await 同步访问、互不中途交错(同 timer.md 的 dispatch 写 / Timer 读模式)。

from collections import deque

from app import gameconfig
from app.wire.server import ChatMessage


class RoomChatBuffer:
    def __init__(self) -> None:
        self._by_room: dict[str, deque[ChatMessage]] = {}  # room → 最近 N 条(定长 deque,满则淘汰最旧)

    def append(self, room: str, msg: ChatMessage) -> None:
        # 房聊广播入缓冲(首条惰性建桶)。次序由 GameLoop 串行保证(dispatch 在 GameLoop 内同步调)。
        self._by_room.setdefault(room, deque(maxlen=gameconfig.ROOM_CHAT_HISTORY_SIZE)).append(msg)

    def recent(self, room: str) -> tuple[ChatMessage, ...]:
        # 该房最近 N 条快照(tuple 不可变,安全发送);无该房记录(从未聊过 / 房不存在)→ 空。
        return tuple(self._by_room.get(room, ()))
