# 0032 · 日志地基:setup_logging + GameLoop 边界审计 + 脱敏红线

日期:2026-06-24 · 范围:`app/shell/logsetup.py`(新:JSON/console formatter + contextvars 关联字段 filter + `setup_logging`)、`app/shell/gameloop.py`(命令进→事件出边界审计 + 关联字段绑定)、`app/shell/lifespan.py`(启动序配日志)、`app/gameconfig.py`(`LOG_LEVEL`/`LOG_FORMAT`/`LOG_FILE` dev 常量)、英文化既有中文日志(`persist.py`/`orm_persister.py`/`receiver.py`)、`tests/shell/test_logsetup.py`(新)+ `tests/shell/test_gameloop.py`(边界审计 + 脱敏红线)。讨「硬化 / 子系统」的 **日志:GameLoop 边界审计 + 脱敏红线** 项。

## 背景 / 为什么

[log.md](../../log.md) 定的日志设计**一处未落地**:全仓 `grep` 无 `setup_logging`/`basicConfig`/formatter/level 配置——root logger 未配,`log.info`/`log.debug` 在默认级别(WARNING)下**根本不输出**,且无 JSON 结构化、无关联字段。0031 加的 `log.critical`(inbox 满)、delayDB 的重试/毒丸/drain 日志,**当前都进不了任何有格式的落点**。同时 [log.md](../../log.md):8 的核心设计「日志挂在 GameLoop 命令进→事件出边界」尚未实现(`gameloop.py` 只在崩溃时 `log.exception`,不记命令受理/业务失败/事件摘要)。另发现既有日志多处中文消息(`persist.py`/`orm_persister.py`/`receiver.py`),违反 [log.md](../../log.md):95「日志内容一律英文」。

> **为何现在做日志、而非配置收编**:[config.md](../../config.md):43 明确「pydantic-settings + poker.env + 无默认 + Field 边界」是 **P8「配置收编」目标形态**,当前 `gameconfig.py` 的「带默认值具名常量」是 dev 阶段有意为之。故日志所需的 `LOG_*` 也照此 dev 常量落,P8 随整个 `gameconfig` 一起 env 化。日志是「硬化 / 子系统」项、独立自洽、且承接 0031(让新加的 CRITICAL 有结构落点),优先级高于尚未到点的配置收编。

## 关键设计决策(批判性,对齐 log.md)

1. **同步直写 + stdlib logging,不开协程/不上 structlog**(log.md:15/63)。日志是本地快 IO,单线程 asyncio 下 `log.*()` 不 `await`、不与扫描交错,无需 Sender/PersistWriter 式队列。`QueueHandler` 兜尾留待实测尾延迟再上(只动 `setup_logging`,业务 `log.*()` 不变)。
2. **关联字段用 contextvars + handler 级 filter「在 log 调用上下文拍到 record 上」**(非在 formatter 里读 contextvar)。这样即便日后上 `QueueHandler`(后台线程格式化),字段已在入队时拍定、不丢——QueueHandler-ready。GameLoop 每条命令 `bind`(cmd_type/nick/room/hand_seq/hand_epoch)→处理→`reset`;`handle` 全程无 `await`,绑定不跨命令泄漏。
3. **审计挂 GameLoop 边界,不在 reduce 内** log(log.md:7/98,守不变量 1)。`handle` 记:命令受理(DEBUG)、业务失败 `Err`(WARNING,带 code+detail,log.md:29「预期内,不是 ERROR」)、未预期异常(ERROR + traceback,已丢工作副本)、事件摘要(DEBUG,**只记类型计数、不序列化 payload**)、手牌里程碑(INFO,`HandStarted`/`HandEnded` 只记 `type` 字面量)。core 仍零日志(grep 复验)。
4. **脱敏 = 结构性 + 纪律**(log.md:49/57):审计**只记摘要**(cmd_type / 事件类型计数 / code / nick / room / hand_seq·epoch),**绝不 `log.debug(cmd)` 整条命令或事件 payload**——`hole_cards`/`deck` 无从进日志。不建脱敏 scrubber(过度):靠「不记 payload」+ 一条**红线测试**(跑一手牌、断言任何级别日志里不出现底牌牌面)兜。
5. **`setup_logging` 只在 lifespan 启动调一次**(connection.md 启动序 step 2),不在 `DevShell.setup()`——避免单测构造 `DevShell` 时改全局 root logger;单测要断言日志用 `caplog`。幂等:重配前清旧 handler。
6. **`LOG_*` dev 常量**:`LOG_LEVEL="INFO"`(production-like;DEBUG 全量审计按需开)、`LOG_FORMAT="console"`(dev 友好;生产 json)、`LOG_FILE=""`(空=stderr)。

## 打算改什么(开工前)

- `app/gameconfig.py`:`LOG_LEVEL`/`LOG_FORMAT`/`LOG_FILE` 三常量(带 dev 默认 + 取值注释)。
- `app/shell/logsetup.py`(新):`_JsonFormatter`/`_ConsoleFormatter`、`_ContextFilter`(contextvars 快照拍到 record)、`bind_log_context`/`reset_log_context`、`setup_logging(level, fmt, file)`。
- `app/shell/gameloop.py`:`handle` 边界审计 + 绑定关联字段;`_event_summary` 辅助(类型计数)。
- `app/shell/lifespan.py`:lifespan 启动先 `setup_logging(...)`。
- 英文化中文日志:`persist.py`(4)、`orm_persister.py`(2)、`receiver.py`(1)。
- `tests/shell/test_logsetup.py`(新):JSON 字段/异常/上下文 filter/console/幂等。
- `tests/shell/test_gameloop.py`:边界审计(caplog 断言命令受理/业务失败 WARNING/异常 ERROR)+ **脱敏红线**(跑动作、断言底牌/牌堆不入日志)。
- 文档:`log.md`(若实现偏离签名则同步)、`TODO.md`(勾日志项)。

## 实际改了什么

- **`app/shell/logsetup.py`(新,~85 行)**:`_JsonFormatter`(`ts`/`level`/`logger`/`msg` + 关联字段/extra + `exc` traceback,`json.dumps(ensure_ascii=False, default=str)`)、`_ConsoleFormatter`(`LEVEL logger msg [k=v…]`)、`_ContextFilter`(handler 级,在 log 调用上下文把 contextvar 字段拍到 record,不覆盖显式 extra → QueueHandler-ready)、`bind_log_context(**fields)`(剔 None)/`reset_log_context(token)`、`setup_logging(level, fmt, file)`(root 级别 + 单 handler[file 或 stderr] + formatter + filter;幂等清旧 handler)。
- **`app/shell/gameloop.py`**:`handle` 边界审计——`bind_log_context(cmd_type/nick/room/hand_seq/hand_epoch)`(取 `work` 与 `work.room.hand`)→ DEBUG「cmd received」→ 异常 ERROR「reduce crashed」(traceback)→ 业务失败 WARNING「cmd rejected: code/detail」→ 成功后 `_audit_applied`(里程碑 `HandStarted`/`HandEnded` INFO 只记 `type`;事件类型计数 DEBUG)→ `finally` `reset_log_context`;新增 `_event_summary`(类型计数,不序列化 payload);import `Broadcast`/`Event`(core.events)、`HandStarted`/`HandEnded`(wire.server)、`bind/reset`(logsetup)。
- **`app/shell/lifespan.py`**:lifespan 启动**第一步** `setup_logging(gameconfig.LOG_LEVEL, LOG_FORMAT, LOG_FILE)`(早于 `shell.setup()`);import `setup_logging`。
- **`app/gameconfig.py`**:`LOG_LEVEL="INFO"`/`LOG_FORMAT="console"`/`LOG_FILE=""`(dev 常量 + 取值注释)。
- **英文化日志消息**(log.md:95):`persist.py`(毒丸/回灌/flushed/drain 超时/未知载荷/NullPersister 共 6 处)、`orm_persister.py`(未知状态写/事件写 2 处)、`receiver.py`(join_room DB 读失败 1 处)——仅改消息文本为英文,中文**代码注释**保留(符合 coding_principle:标识符/提交/日志英文,注释中文)。
- **`tests/shell/test_logsetup.py`(新,8 测)**:JSON 基础字段/显式 extra/异常字段、ContextFilter 拍字段+剔 None+不覆盖显式 extra、reset 清上下文、console 行+extra、setup_logging 幂等单 handler(测后复原 root)。
- **`tests/shell/test_gameloop.py`(+4 测)**:边界审计「cmd received」带关联字段(caplog 挂 `_ContextFilter`)+「cmd applied」;业务失败 = WARNING 非 ERROR 带 code;reduce 异常 = ERROR 带 traceback;**脱敏红线**——驱 `StartHand`(产 `HoleCards`/`HandStarted` 携底牌的事件),断言审计跑了(milestone + applied)但任何日志里**无玩家底牌牌面、无 deck**。
- **文档**:本记录 + `TODO.md`(勾日志项)。`log.md` 实现与设计一致(同步直写 + contextvars + 边界审计 + 脱敏),无签名偏离,未改。

实测样例(JSON):`{"ts":…,"level":"WARNING","logger":"app.shell.gameloop","msg":"cmd rejected: code=NOT_YOUR_SEAT detail=…","cmd_type":"SitDown","nick":"alice","room":"r1","hand_seq":3,"hand_epoch":2}`;290 全绿。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 7 维 review **子代理工作流**(各维 1 审查者 → 每候选 1 反驳者;含脱敏/分层/控制流隔离三高风险面专项)。结论 **go:17 候选 / 0 must-fix,全部反驳后仅余 nit**——三大高风险面均判 sound。逐维:

- **① 分层 / 不变量**:`grep app/core` 复验**零 logging**(invariant 1 守住:logsetup/审计全在 `app/shell`,reduce 不 import logging,边界审计设计正是为了「不把 log 推进 reduce 分支」)。**控制流隔离**(最强反驳点「bind 在 try 外是否跨命令泄漏」)被驳回:`bind_log_context` 只写 contextvar、不会有意义地抛;`handle` 全程 await-free 单线程 asyncio → contextvars 按命令隔离;`reset_log_context` 在 `finally`;formatter `json.dumps(default=str)` 降级不崩 → 日志是旁路、绝不改命令处理结果(log.md invariant 4)。
- **② 代码↔文档**:实现与 log.md 一致(同步直写 + contextvars + 边界审计 + 脱敏 + 英文);唯一 nuance——log.md:84-89 把 `LOG_*` 画成 pydantic BaseSettings(P8 目标形态),本批落 dev 常量(config.md:43 已授权 dev 阶段如此),**已在 log.md:91 补一行当前状态交叉链**消歧。
- **③ 文档↔文档**:`TODO.md` 勾日志项 + 计数 290;log.md / config.md / gameconfig 三处「LOG_* 现为 dev 常量、P8 env 化」一致。
- **④ 数据模型 / formatter 正确性**:`_STD_LOGRECORD_ATTRS` 用 `frozenset(makeLogRecord({}).__dict__)` 动态取标准属性 + 显式并 `message`/`asctime`(getMessage/formatTime 后补、基础 __dict__ 不含)——实测 `_record_extras` 只吐绑定字段(`room`/`cmd_type`…);nit「`taskName` 冗余」已采纳删去(3.12 基础集已含,字节等价)。`_ContextFilter` 挂 handler 级 → 子 logger 传播来的 record 也拍字段。
- **⑤ 规范**:`LOG_*` 带取值语义注释;`json`/`console` 是 `LOG_FORMAT` 文档化取值(非魔法串);英文化只改消息文本、中文代码注释保留(符合 coding_principle);无死代码。gameloop import wire `HandStarted`/`HandEnded` 同 dispatch 既有 wire import,合分层。
- **⑥ 测试**:**脱敏红线非恒真**——驱 `StartHand` 真产携底牌的 `HoleCards`/`HandStarted` 事件(断言 milestone+applied 跑过),再断言任何日志无牌面;已采纳 nit **把牌面来源从重算公式改为读已提交 `hand.players[*].hole_cards` + 断言 `len==2`**(发牌/座位数变了自适应,杜绝假绿)+ 补「牌堆」中文 detail 不入日志;`test_logsetup` 幂等测已采纳 nit **加断言 `_ContextFilter` 已挂 handler**(防 refactor 漏挂 filter 不被发现)。
- **⑦ 账本**:打算↔实际差异 + 3 条 nit 采纳已记;TODO 勾项 + 计数;提交引用 `0032`、全英文。

**对抗核实存活 / 驳回**:全部候选驳到 nit。*采纳的 nit(4 条,均已在本批修)*:(a) 删冗余 `taskName`;(b) 脱敏测牌面来源改读已发底牌 + `len==2`;(c) `test_logsetup` 加 `_ContextFilter`-on-handler 断言;(d) 脱敏测补「牌堆」中文 detail 不入 + log.md 交叉链。*驳回的关键候选*:「reduce.py 牌堆不足 Err 泄露 deck」——驳回:该 Err detail 只带**张数计数**(`牌堆 N 张`),非牌面;log.md 红线护的是**内容**,记数不违规。`workflow 一个反驳器 schema 重试超限`(1 个 candidate 未出结论)不影响结论——该维其余反驳器 + 综合判定已覆盖。

> 批判性自评:本批最高风险是脱敏红线,而**真正的护栏是结构性**——审计只记 `type(ev).__name__` 计数与 `ev.msg.type` 字面量,从不序列化 payload,故底牌/牌堆无从进日志(单测只是这条结构保证的可执行见证)。review 把测试的牌面来源从「重算公式」钉到「读真实发牌」,正是 review.md「绿测覆盖想到的、review 覆盖没想到的」:原测虽不假绿,但来源与被测逻辑解耦后更抗未来回归。

## 待办 / 下一步

- `QueueHandler` 兜尾(实测日志 IO 拖循环才上)。
- 配置收编(P8):`LOG_*` 随 `gameconfig` 整体 env 化(pydantic-settings + poker.env + 无默认)。
- 各 IO 协程(Receiver/Sender/PersistWriter/Timer)的 IO 成败日志已具雏形(0024+),可按 log.md「谁记什么」表细化级别。
