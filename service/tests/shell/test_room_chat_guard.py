"""房聊进 reduce 前的 shell 文本防护 + 限速(messaging.md 契约 4 / changes/0033):
空 / 超长内容先拒(不耗令牌、不到 GameLoop),内容合法再过令牌桶;失败回对应 Err、过则构 RoomChat 命令。"""

import time

from app import gameconfig
from app.core.commands import RoomChat
from app.shell.receiver import _guard_room_chat
from app.wire import client as wire_client
from tests.shell._fakes import drain, make_conn


def _chat(text: str) -> wire_client.RoomChat:
    return wire_client.RoomChat(text=text)


def test_valid_chat_passes_and_builds_command_with_connection_nick():
    conn = make_conn("alice")
    cmd = _guard_room_chat(conn, _chat("hello table"))
    assert isinstance(cmd, RoomChat) and cmd.origin == "alice" and cmd.text == "hello table"  # 身份盖连接 nick
    assert drain(conn) == []  # 无 Err 回发


def test_empty_text_rejected_invalid_message():
    conn = make_conn("alice")
    assert _guard_room_chat(conn, _chat("   ")) is None  # 纯空白 = 空
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "INVALID_MESSAGE"


def test_too_long_text_rejected_message_too_long():
    conn = make_conn("alice")
    over = "x" * (gameconfig.ROOM_CHAT_MAX_TEXT_LEN + 1)
    assert _guard_room_chat(conn, _chat(over)) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "MESSAGE_TOO_LONG"


def test_at_max_length_passes():
    conn = make_conn("alice")
    exact = "y" * gameconfig.ROOM_CHAT_MAX_TEXT_LEN
    cmd = _guard_room_chat(conn, _chat(exact))
    assert isinstance(cmd, RoomChat) and cmd.text == exact  # 边界(== MAX)放行


def test_rate_limited_when_bucket_empty():
    conn = make_conn("alice")
    conn.chat_bucket.tokens = 0
    conn.chat_bucket.updated_at = time.monotonic() + 3600  # 基准设到未来 → 守护内 try_consume 的 elapsed 恒 0,不补
    assert _guard_room_chat(conn, _chat("spam")) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "RATE_LIMITED"


def test_invalid_content_does_not_consume_token():
    # 决策 2:内容非法的帧根本不到 GameLoop,故先拒、不耗令牌(令牌只防合法消息刷屏)。
    conn = make_conn("alice")
    before = conn.chat_bucket.tokens
    _guard_room_chat(conn, _chat("z" * (gameconfig.ROOM_CHAT_MAX_TEXT_LEN + 1)))  # 超长
    _guard_room_chat(conn, _chat("  "))  # 空
    assert conn.chat_bucket.tokens == before  # 两次非法均未扣令牌


def test_leading_trailing_whitespace_preserved():
    # strip 仅作「非空」判据;广播**原文**(不改用户内容)——钉死 strip-判但发原文的契约。
    conn = make_conn("alice")
    cmd = _guard_room_chat(conn, _chat("  hi there  "))
    assert isinstance(cmd, RoomChat) and cmd.text == "  hi there  " and drain(conn) == []  # 原样保留首尾空白


def test_burst_then_throttled_through_guard():
    # 满桶突发恰 BURST 条放行、其余被限速(同步循环,微秒级 elapsed 补充可忽略 → 精确等于 BURST)。
    conn = make_conn("alice")
    burst = int(gameconfig.ROOM_CHAT_RATE_BURST)
    passed = sum(1 for _ in range(burst + 3) if _guard_room_chat(conn, _chat("hi")))
    assert passed == burst  # 恰好满桶条数放行(钉死 capacity,杜绝 off-by-one)
    assert any(e.code.value == "RATE_LIMITED" for e in drain(conn))  # 余者限速拒
