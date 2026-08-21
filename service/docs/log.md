# 日志模块

## 设计思路:日志是 shell 的旁路观察者,不是 core 的副作用

日志既是 IO 又要读墙钟,这两件事都被不变量 1 挡在 core 之外——在 reduce 里 `import logging` 等同于在 core 里做 IO。core 想暴露什么,就返回 `Event`。

由此得到两条布置:

- 日志挂在 GameLoop 的「命令进 → 事件出」边界。每条命令、产出的事件、是否抛异常,全在这一处可见,天然就是完整的状态机审计流,不必在业务分支里到处插 `log.info`。
- 各 IO 协程(Receiver / Sender / PersistWriter / Timer)各自记录自己那段 IO 的成败。

## 与架构的契约(必须守住)

1. core 内禁止任何日志调用(不变量 1)。
2. 日志不开单独协程,默认同步直写。理由:它是本地快速 IO,不像 `ws.send` 或 DB commit 那样需要协程加队列。单线程 asyncio 下 `log.*()` 不 `await`,与扫描不交错,stdlib logging 本身线程安全。唯一风险是慢盘、轮转或磁盘满带来的尾延迟,应对办法见下文可选的 `QueueHandler`。
3. 不记录破坏公平性的隐私状态:`hole_cards`、`deck` 任何级别都不进日志,详见「脱敏」。
4. 日志是旁路,不是控制流。任何路径都不得因为日志没记成而改变行为;记录失败只能降级吞掉。

## 谁记什么

| 协程 | 记录内容 | 级别 |
|---|---|---|
| **GameLoop**(核心审计点) | 每条命令受理(记 `cmd_type` 加目标 room/nick);事件摘要(只记类型计数,不含隐私字段);手牌里程碑(开局、结束、胜者、边池);未预期异常(记 traceback、触发命令,以及「已丢弃工作副本」这一事实) | DEBUG / INFO / ERROR |
| **Receiver** | 连接建立/断开;协议解析失败 | INFO / WARNING |
| **Sender** | 发送失败(连接已断,触发 `Disconnect`) | WARNING |
| **PersistWriter** | 落库成功;失败重试;毒丸丢弃;drain 失败 | DEBUG(成功)/ ERROR(失败重试、毒丸丢弃)/ CRITICAL(drain 失败) |
| **Timer** | 投出 `Timeout` / `Cleanup`;被 staleness(过期检查)忽略的那条命令,记录在 GameLoop 里 | DEBUG |

> 业务校验失败(reduce 返回的 `Err`)由 GameLoop 在边界统一记 WARNING,带 `code` + room + nick。它们是预期内的,不是 ERROR。

## 级别约定

| 级别 | 含义 | 例子 |
|---|---|---|
| DEBUG | 全量审计,仅开发/排障开 | 每条命令、每个事件、每次 Timer 触发、每次落库 |
| INFO | 正常业务里程碑 | 手牌开局/结束/胜者、买入、加入/离桌、连接/断开 |
| WARNING | 预期内的异常路径 | 业务校验失败、staleness 丢弃的过期命令、慢客户端被丢弃、落库重试 |
| ERROR | 需人介入但已隔离 | reduce 未预期异常(已丢弃工作副本)、落库失败、Sender 发送异常 |
| CRITICAL | 进程级不可恢复 | GameLoop task 意外退出、drain 失败、配置缺失启动失败 |

> 「GameLoop task 意外退出」这条由 `lifespan` 给三条常驻协程(GameLoop / Timer / PersistWriter)挂的 done-callback 兑现:非取消而退出即落 CRITICAL(0083 之前只有这行文档,没有实现——退出只会在 GC 时以 asyncio 的 "Task exception was never retrieved" 冒个泡)。

## 结构化日志 + 上下文绑定

日志一律结构化成 JSON,便于内网用 `jq` 按字段过滤。

- 每条日志尽量带上关联字段:谁、哪个房间、哪一手、什么命令。GameLoop 取到命令后用 `contextvars` 绑定 `room` 等字段,该命令处理期间的所有日志自动带上这些字段,无需层层传参。
- 除 `room` / `nick` / `cmd_type` 外,再带上 `hand.seq`(手牌标识)与 `hand.epoch`(回合,见 [core.md](core.md))就能把一手牌的日志串成时间线。这两个字段本来就是 core 为 staleness 与幂等引入的内存计数,日志直接复用即可。
- 选型:stdlib `logging` 加一个 JSON formatter 就够;想要顺手的上下文绑定可以上 `structlog`。消息文本讲「发生了什么」,结构字段承载定位信息。

## 脱敏(硬红线)

```
禁止进入日志的字段:hole_cards, deck, password, K_user(共享密钥), session_token
```

后三者是鉴权秘密,见 [auth.md](auth.md)。它们与底牌、牌堆同级:任何级别、任何摘要都不得出现。

具体做法:

- 记录命令或事件摘要时不序列化整个 payload,只记类型加非隐私标识:谁、哪个房间、动作类型、下注额。积分额可以记。
- 想 `log.debug(cmd)` 打整条命令前,必须先确认它不含底牌或牌堆。若某条命令为了重放而携带了洗好的牌堆或 seed,该字段同样禁记。
- 出站 wire DTO 的脱敏靠结构性缺位:[app/wire/server.py](../app/wire/server.py) 里的广播和公开消息根本没有 `hole_cards`/`deck` 字段,见 [changes/0017](refactor/changes/0017-wire-first-batch.md) 决策 2。因此 `model_dump(mode="json")` 自然不含隐私,可以直接作安全摘要的来源。
- 原型 `pokertable/models.py` 曾用 `field_serializer` 抹除这些字段,已随 0027 拆除。

## 落地方式:同步直写为默认,`QueueHandler` 为可选兜尾

默认做法:进程启动时配一次 stdlib `logging`,包括 JSON formatter 和文件或 stderr handler;各协程直接 `log.info(...)`。本规模够用。

可选升级,仅当实测到日志 IO 拖住循环时才做:用 `QueueHandler` + `QueueListener` 把落地 IO 移到后台线程——是线程,不是协程。两种方式切换只动 `setup_logging`,业务代码的 `log.*()` 不变。

```python
import logging, logging.handlers, queue
_log_queue: "queue.Queue" = queue.Queue(-1)

def setup_logging(level, handlers) -> logging.handlers.QueueListener:
    root = logging.getLogger(); root.setLevel(level)
    root.addHandler(logging.handlers.QueueHandler(_log_queue))   # 热路径只入队,不碰磁盘
    listener = logging.handlers.QueueListener(_log_queue, *handlers, respect_handler_level=True)
    listener.start()
    return listener      # 进程退出时 listener.stop() 冲刷
```

时间戳取墙钟即可——这是 shell,不是 core。

## 配置驱动(照 [config.md](config.md))

`LOG_LEVEL` / `LOG_FORMAT` / `LOG_FILE` 已随配置收编(0042),在 [app/gameconfig.py](../app/gameconfig.py) 里转为 env 驱动。

- `LOG_LEVEL` 与 `LOG_FORMAT` 用 `Literal` 收敛取值,生产用 `json`、本地用 `console`;`LOG_FILE` 为空串表示只写 stderr。
- 值放在 `app/poker.env.example`,见 [config.md](config.md);业务代码只引用 `gameconfig.LOG_LEVEL` 等,不内联字面量。
- `setup_logging(level, fmt, file)` 在 lifespan 启动序的第一步调用,见 [connection.md](connection.md)「配置日志」。

## 注意点

- 日志内容一律英文:消息文本、字段名、`code` 全用英文,便于检索、跨环境一致。面向玩家的中文文案是前端按 `code` 映射的事。
- `reduce` 内出现 `logging` / `print` 即越层,提交前自检拦下。
