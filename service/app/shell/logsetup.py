# 日志装配(见 log.md):shell 旁路观察者,JSON/console 双格式 + contextvars 关联字段。
# core 绝不 import 本模块(不变量 1:core 无日志);仅 shell 各协程用。同步直写(本规模够用;
# QueueHandler 兜尾留待实测尾延迟再上,届时只动 setup_logging,业务 log.*() 不变)。

import contextvars
import json
import logging
import sys
from typing import Any

# GameLoop 边界绑定的关联字段(room/nick/cmd_type/hand_seq/hand_epoch),该命令处理期间所有日志自动带上。
# 单线程 asyncio:GameLoop.handle 全程无 await,bind→reset 不与他协程上下文交错(contextvars 本就按上下文隔离)。
_log_context: "contextvars.ContextVar[dict[str, Any]]" = contextvars.ContextVar("log_context", default={})


def bind_log_context(**fields: Any) -> "contextvars.Token[dict[str, Any]]":
    # 绑定关联字段(剔除 None);返回 token 供 reset 复原(GameLoop 每条命令一对 bind/reset)。
    return _log_context.set({k: v for k, v in fields.items() if v is not None})


def reset_log_context(token: "contextvars.Token[dict[str, Any]]") -> None:
    _log_context.reset(token)


# 在 log 调用所在上下文(同步)把关联字段拍到 record 上 —— 即便日后 QueueHandler 后台格式化也带得上、不丢。
class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _log_context.get().items():
            if not hasattr(record, key):  # 不覆盖显式 extra
                setattr(record, key, value)
        return True


# LogRecord 自带的标准属性(不当结构字段输出);其余 record.__dict__ 键 = 关联字段 / 显式 extra。
# `message`/`asctime` 由 getMessage()/formatTime() 后补,基础 __dict__ 不含,故显式并入(taskName 等已在基础集内)。
_STD_LOGRECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STD_LOGRECORD_ATTRS}


class _JsonFormatter(logging.Formatter):
    # 结构化 JSON 一行一条:固定 ts/level/logger/msg + 关联字段/extra + 异常 traceback。
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record),  # 墙钟(shell 许可;core 才禁读钟)
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(_record_extras(record))  # 关联字段(filter 拍上)+ 显式 extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ConsoleFormatter(logging.Formatter):
    # 本地友好单行:LEVEL logger msg [k=v ...];关联字段/extra 附尾。
    def format(self, record: logging.LogRecord) -> str:
        line = f"{record.levelname:<8} {record.name} {record.getMessage()}"
        extras = _record_extras(record)
        if extras:
            line += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str, fmt: str, file: str = "") -> None:
    # 进程启动配一次(lifespan 启动序,见 connection.md「配置日志」):root 级别 + 单 handler(file 或 stderr)+ 格式
    # + 关联字段 filter。幂等:重配前清旧 handler(避免重复叠加)。失败降级不在此——配置失败应启动即报错。
    root = logging.getLogger()
    root.setLevel(level)
    for old in list(root.handlers):
        root.removeHandler(old)
    handler: logging.Handler = (
        logging.FileHandler(file, encoding="utf-8") if file else logging.StreamHandler(sys.stderr)
    )
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _ConsoleFormatter())
    handler.addFilter(_ContextFilter())  # handler 级:本 handler 处理的所有 record(含子 logger 传播来的)都拍字段
    root.addHandler(handler)
