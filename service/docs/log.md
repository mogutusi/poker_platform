# 日志模块

## 设计思路:日志是 shell 的旁路观察者,不是 core 的副作用

日志的本质是 **IO + 读墙钟(时间戳)**,两样都被不变量 1 挡在 core 之外。所以:

- **core(`reduce`)绝不写日志。** 它是纯同步函数,想暴露什么就返回 `Event`;在 reduce 里 `import logging` 等同于在 core 里做 IO。
- **日志挂在 GameLoop 的「命令进 → 事件出」边界**:每条命令是什么、产出哪些事件、是否抛异常,全在这一处可见。挂在这里等于零成本拿到一条**完整的状态机审计流**,不必在业务分支里到处插 `log.info`。

一句话:**日志是挂在 GameLoop 边界上的旁路观察者**,外加各 IO 协程(Receiver / Sender / PersistWriter / Timer)记录各自 IO 成败。它**只读不改**,绝不参与控制流。

## 与架构的契约(必须守住)

1. **core 内禁止任何日志调用**(不变量 1)。需要记录的,要么已是 `Event`,要么由 GameLoop 在边界据命令与事件推断。
2. **日志不给单独协程,默认同步直写。** 它是 IO,但是**本地、快、不依赖远端**的 IO——不像 `ws.send` / DB commit,不该像 Sender / PersistWriter 那样开协程 + 队列。单线程 asyncio 下 `log.*()` 不 `await`、与扫描不交错,无正确性冲突(stdlib logging 本身线程安全)。唯一风险是慢盘/轮转/磁盘满的**尾延迟**,要兜见下文可选 `QueueHandler`(后台线程,不是协程)。
3. **绝不记录破坏公平性的隐私状态**:`hole_cards`、`deck` **任何级别都不进日志**——日志泄露底牌等同作弊。详见「脱敏」。
4. **日志是值的旁路,不是控制流**:任何代码路径都不得「因为没记成日志就改变行为」。记录失败只能降级吞掉。

## 谁记什么

| 协程 | 记录内容 | 级别 |
|---|---|---|
| **GameLoop**(核心审计点) | 每条命令受理(`cmd_type` + 目标 room/nick);产出事件摘要(类型计数,**不含隐私字段**);手牌里程碑(开局/结束/胜者/边池);**未预期异常**(traceback + 触发命令 + 已丢弃工作副本) | DEBUG / INFO / ERROR |
| **Receiver** | 连接建立/断开;协议解析失败 | INFO / WARNING |
| **Sender** | 发送失败(连接已断,触发 `Disconnect`) | WARNING |
| **PersistWriter** | 落库成功(DEBUG)/ 失败重试(ERROR)/ 毒丸丢弃(ERROR)/ drain 失败(CRITICAL) | DEBUG / ERROR / CRITICAL |
| **Timer** | 投出 `Timeout` / `Cleanup`;被 staleness 忽略的不在这里记(在 GameLoop 记) | DEBUG |

> **业务校验失败**(reduce 返回的 `Err`)由 GameLoop 在边界统一记 **WARNING**,带 `code` + room + nick。它们是预期内的,不是 ERROR。

## 级别约定

| 级别 | 含义 | 例子 |
|---|---|---|
| **DEBUG** | 全量审计,仅开发/排障开 | 每条命令、每个事件、每次 Timer 触发、每次落库 |
| **INFO** | 正常业务里程碑 | 手牌开局/结束/胜者、买入、加入/离桌、连接/断开 |
| **WARNING** | 预期内的异常路径 | 业务校验失败、staleness 丢弃的过期命令、慢客户端被丢弃、落库重试 |
| **ERROR** | 需人介入但已隔离 | reduce 未预期异常(已丢弃工作副本)、落库失败、Sender 发送异常 |
| **CRITICAL** | 进程级不可恢复 | GameLoop task 意外退出、drain 失败、配置缺失启动失败 |

## 结构化日志 + 上下文绑定

日志一律 **结构化(JSON)**,便于内网用 `jq` 按字段过滤。每条尽量带**关联字段**(谁、哪个房间、哪一手、什么命令)。用 `contextvars` 在 GameLoop 取到命令后绑定 `room` 等关联字段,该命令处理期间所有日志自动带上,无需层层传参。

> **关联字段串起「一手牌的时间线」**:除 `room` / `nick` / `cmd_type` 外,带上 `hand.seq`(手牌标识)与 `hand.epoch`(回合,见 [core.md](core.md)),即可把一手牌的所有日志串成一条线。这两个字段正是 core 为 staleness/幂等引入的内存计数,日志直接复用。
>
> 选型:stdlib `logging` + JSON formatter 足够;想要顺手的上下文绑定可上 `structlog`。**消息文本讲「发生了什么」,结构字段承载「定位信息」**。

## 脱敏(硬红线)

```
禁止进入日志的字段:hole_cards, deck, password, K_user(共享密钥), session_token
```

> 后三者是鉴权秘密(见 [auth.md](auth.md)),与底牌/牌堆同级红线:任何级别、任何摘要都不得出现。

- 记录命令/事件摘要时**不序列化整个 payload**,只记类型 + 非隐私标识(谁、哪个房间、动作类型、下注额——积分额可记)。
- 想 `log.debug(cmd)` 整条命令前**必须确认它不含底牌/牌堆**。若某命令为重放携带了洗好的牌堆 / seed,该字段同样禁记。
- 复用 [models.py](../app/pokertable/models.py) 的 `field_serializer` 隐藏逻辑:`model_dump(mode="json")` 出来的已抹掉底牌/牌堆,可作「安全摘要」来源。

## 落地方式:同步直写为默认,`QueueHandler` 为可选兜尾

**默认同步直写**:进程启动配一次 stdlib `logging`(JSON formatter + 文件/stderr handler),各协程直接 `log.info(...)`。本规模够用。

**可选兜尾**(仅当慢盘/轮转/磁盘满的尾延迟拖住循环时):用 `QueueHandler` + `QueueListener` 把落地 IO 移到**后台线程**(线程,**不是协程**):

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

- **先上同步直写**,实测到日志 IO 拖循环才升级;两种切换只动 `setup_logging`,业务的 `log.*()` 不变。
- 时间戳取墙钟即可——**这是 shell,不是 core**。

## 配置驱动(照 [config.md](config.md))

```python
class GameConfig(BaseSettings):
    LOG_LEVEL: str   = Field(pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    LOG_FORMAT: str  = Field(pattern="^(json|console)$")   # 生产 json,本地 console
    LOG_FILE: str    # 落地文件路径;空串表示只 stderr
```

业务代码只引用 `gameconfig.LOG_LEVEL` 等,绝不内联字面量。

## 注意点

- **日志内容一律英文**:消息文本、字段名、`code` 全用英文(便于检索、跨环境一致)。面向玩家的中文文案是前端按 `code` 映射的事。
- **不在 core 里 log**:看到 `reduce` 内出现 `logging` / `print` 等同越层,提交前自检拦下。
- **底牌/牌堆零容忍**:任何级别、任何摘要都不得出现 `hole_cards` / `deck`。
- **审计优先挂边界**:别在 reduce 每个 if 分支塞 log——状态机的「发生了什么」在 GameLoop 边界已能完整看到。
- **预期 vs 意外分级**:`Err`(业务失败)是 WARNING,reduce 抛异常(bug)才是 ERROR。
- **失败降级**:日志落地失败只能吞掉 + 尽力告警,绝不反过来影响命令处理结果。
