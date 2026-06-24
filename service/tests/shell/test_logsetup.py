"""日志装配(log.md):JSON/console formatter + contextvars 关联字段 filter + setup_logging 幂等。"""

import json
import logging
import sys

from app.shell.logsetup import (
    _ConsoleFormatter,
    _ContextFilter,
    _JsonFormatter,
    bind_log_context,
    reset_log_context,
    setup_logging,
)


def _record(msg: str = "hi", level: int = logging.INFO, name: str = "app.shell.test", **extra) -> logging.LogRecord:
    rec = logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_json_formatter_basic_fields():
    obj = json.loads(_JsonFormatter().format(_record("hello", name="app.shell.gameloop")))
    assert obj["level"] == "INFO" and obj["logger"] == "app.shell.gameloop" and obj["msg"] == "hello"
    assert "ts" in obj  # 墙钟(shell)


def test_json_formatter_includes_explicit_extras():
    obj = json.loads(_JsonFormatter().format(_record("m", room="r1", nick="alice")))
    assert obj["room"] == "r1" and obj["nick"] == "alice"


def test_json_formatter_exception_field():
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("app.t", logging.ERROR, __file__, 1, "crashed", (), sys.exc_info())
    obj = json.loads(_JsonFormatter().format(rec))
    assert "exc" in obj and "ValueError" in obj["exc"]


def test_context_filter_snaps_bound_fields_and_drops_none():
    token = bind_log_context(room="r1", cmd_type="SitDown", nick=None)  # None 被剔除
    try:
        rec = _record("m")
        _ContextFilter().filter(rec)
        obj = json.loads(_JsonFormatter().format(rec))
        assert obj["room"] == "r1" and obj["cmd_type"] == "SitDown" and "nick" not in obj
    finally:
        reset_log_context(token)


def test_context_filter_does_not_override_explicit_extra():
    token = bind_log_context(room="ctx")
    try:
        rec = _record("m", room="explicit")  # 显式 extra 优先于绑定上下文
        _ContextFilter().filter(rec)
        assert rec.room == "explicit"
    finally:
        reset_log_context(token)


def test_reset_clears_context():
    token = bind_log_context(room="r1")
    reset_log_context(token)
    rec = _record("m")
    _ContextFilter().filter(rec)
    assert "room" not in json.loads(_JsonFormatter().format(rec))  # reset 后不再带


def test_console_formatter_line_and_extras():
    token = bind_log_context(room="r1")
    try:
        rec = _record("hello")
        _ContextFilter().filter(rec)
        line = _ConsoleFormatter().format(rec)
        assert "INFO" in line and "hello" in line and "room=r1" in line
    finally:
        reset_log_context(token)


def test_setup_logging_idempotent_single_handler():
    # 重配清旧 handler、不叠加;改全局 root,测后复原避免污染其它测试。
    root = logging.getLogger()
    saved, saved_level = list(root.handlers), root.level
    try:
        setup_logging("DEBUG", "json", "")
        assert len(root.handlers) == 1 and isinstance(root.handlers[0].formatter, _JsonFormatter)
        assert any(isinstance(f, _ContextFilter) for f in root.handlers[0].filters)  # 关联字段 filter 已挂
        setup_logging("INFO", "console", "")
        assert len(root.handlers) == 1  # 不叠加
        assert isinstance(root.handlers[0].formatter, _ConsoleFormatter) and root.level == logging.INFO
    finally:
        root.handlers[:] = saved
        root.setLevel(saved_level)
