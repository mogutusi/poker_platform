"""房聊环形缓冲(messaging.md §持久化 / changes/0036):每房定长、旧→新、淘汰最旧、快照不可变。"""

from app import gameconfig
from app.shell.history import RoomChatBuffer
from app.wire.server import ChatMessage


def _msg(text: str) -> ChatMessage:
    return ChatMessage(from_nick="alice", text=text)


def test_empty_room_returns_empty_tuple():
    assert RoomChatBuffer().recent("r1") == ()  # 从未聊过 / 房不存在 → 空


def test_append_and_recent_per_room_in_order():
    b = RoomChatBuffer()
    b.append("r1", _msg("a"))
    b.append("r1", _msg("b"))
    b.append("r2", _msg("x"))
    assert tuple(m.text for m in b.recent("r1")) == ("a", "b")  # 旧→新
    assert tuple(m.text for m in b.recent("r2")) == ("x",)  # 每房独立


def test_ring_evicts_oldest_at_capacity():
    b = RoomChatBuffer()
    n = gameconfig.ROOM_CHAT_HISTORY_SIZE
    for i in range(n + 5):
        b.append("r1", _msg(str(i)))
    recent = b.recent("r1")
    assert len(recent) == n  # 定长封顶
    assert recent[0].text == "5" and recent[-1].text == str(n + 4)  # 淘汰最旧 5、留最近 N


def test_recent_is_immutable_snapshot():
    b = RoomChatBuffer()
    b.append("r1", _msg("a"))
    snap = b.recent("r1")
    b.append("r1", _msg("b"))  # 取快照后再写,不影响已取的
    assert isinstance(snap, tuple) and tuple(m.text for m in snap) == ("a",)
