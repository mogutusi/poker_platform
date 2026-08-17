# 编码规范(总纲 · 先读我)

> 在本项目(`service/`)写代码前先读完这一篇。它是所有约定的入口,每条规则都链到落地细节。违反「硬规则」的改动不接受。

## 阅读顺序

1. 本篇 —— 全部规则一览
2. [architecture.md](architecture.md) —— 分层、并发模型、不变量
3. [storage.md](storage.md) —— 内存权威 + 工作副本回滚 + delayDB
4. [core.md](core.md) —— 游戏状态机;细则见 [rules.md](rules.md)(盲注/下注轮关闭/边池 + 测试)
5. 按要改的模块再读:[connection.md](connection.md) · [lobby.md](lobby.md) · [messaging.md](messaging.md) · [presence.md](presence.md) · [rest.md](rest.md) · [wire.md](wire.md) · [models.md](models.md) · [timer.md](timer.md) · [error.md](error.md) · [config.md](config.md) · [log.md](log.md) · [user.md](user.md) · [db.md](db.md) · [auth.md](auth.md)
6. 工程:[dev.md](dev.md)(Poetry/Alembic/环境) · [db-migrations.md](db-migrations.md) · [testing.md](testing.md) · [review.md](review.md)(push 前必走)

## 核心模型(一句话)

**内存权威 + 单写者 + 工作副本回滚。**

- `world` 是内存权威,装着全部房间 + 全局用户积分;唯一的 GameLoop 串行处理命令。
- 每条命令先把工作集深拷贝成**工作副本**:工作集 = 目标房间 + users 表,工作副本 = 这次命令的状态草稿。
- `reduce` 只改副本:成功就 commit 回 `world`,失败或异常就整份丢弃。
- 需要落库的数据走 delayDB 通道异步追平 DB。delayDB = 先写内存缓冲、再由后台协程成批写库的那条通道。

## 硬规则(违反即打回)

逐条对应 [architecture.md](architecture.md) 的不变量:

1. **分层不可逆**。`core`(游戏规则)纯同步:禁止 `async`/`await`/IO/DB/`sleep`/读墙钟,且不 import 任何 FastAPI/SQLAlchemy/WebSocket 符号;IO 只在 `shell`。
2. **`reduce` 原子**。处理一条命令期间不 `await`;只改工作副本,成功才 commit,失败或异常整份丢弃,结果是 `world` 要么全改、要么一字节不动。失败安全由「丢弃副本」保证,所以不强制先校验后改;但建议校验前置,图清晰。详见 [error.md](error.md)。
3. **事件不持有会被改写的引用**。跨命令的隔离由工作副本 commit 天然保证,要守的只是:同一条 reduce 内产出 event 之后,别再改它引用的对象;事件一般在末尾构造,自然满足。
4. **错误是返回值(`Err`),不是事件、不用异常做控制流**。签名是 `reduce -> (events, Err | None)`,成功与失败互斥;业务校验失败 `return [], Err(code, detail)`,由 GameLoop 回发命令发起人(`origin`)。core helper 之间也用 Go 风格:`Err | None` 或 `(value, Err)`,绝不 `raise`;异常只留给真正的 bug。详见 [error.md](error.md)。
5. **定时器/连接/断线一律转 `Command` 进 `inbox`**。不在后台 task 里直接改 `world`,不旁路 `ws.send`。详见 [timer.md](timer.md)。
6. **对外发送只经 per-connection Sender 队列**。禁止 `create_task(ws.send())`,也禁止在别处直接 `ws.send()`:会破坏保序,还可能阻塞。
7. **持久化只走「内存权威 + delayDB」**。需落库的数据从 DB 读一次进内存,改内存,产出 `Persist` 异步落库;载入决策在 reduce 里做,shell 不读 `world`。唯一写者,因此无行锁、无 `with_for_update`。详见 [storage.md](storage.md) / [db.md](db.md) / [user.md](user.md)。
8. **每条命令只作用于一个房间**。工作副本与回滚据此界定,跨房间操作另议;一个用户也只在一个房间,见 `UserState.room`,`Connect` 到别房即拒——这样全局积分驱逐才无歧义。详见 [user.md](user.md)。
9. **不硬编码可调参数**。超时、盲注/买入上下限、tick、flush 周期等必须进配置,代码只引用 `gameconfig.XXX`。详见 [config.md](config.md)。

## 通用规范

- **无魔法数字**:裸字面量(`15`、`0.5`、`1000`)不进业务逻辑;可调的进配置,真常量写成具名常量如 `HOLE_CARD_COUNT = 2`,名字或注释标明单位如 `*_MS`、`*_SECONDS`。
- **类型标注齐全**:函数签名、模型字段都带类型;有结构的数据用 Pydantic/dataclass,别用裸 dict/tuple。
- **命名表意**:避免 `data`/`tmp`/`x`;布尔用 `is_`/`has_` 前缀。
- **不留死代码**:不提交 `.bak`、注释掉的大段代码、调试 `print`。原型死代码已随 [0027](refactor/changes/0027-prototype-teardown.md) 清理。
- **风格随邻里**:新代码的命名、注释密度、缩进、idiom 与所在文件一致。
- **注释讲「为什么」**:解释意图与不变量,不复述代码;边池、超时 staleness(过期作废)这类反直觉处必须有注释。
- **注释一律用中文**:代码注释、docstring 都用中文;只有标识符和提交信息用英文(见 [dev.md](dev.md))。
- **字段必须标注含义,模块不写大段 docstring**。
  - 每个 `dataclass`/Pydantic 字段、每个枚举(`Enum`/`StrEnum`)成员都要一句简短的行内中文注释注明含义,并带上单位/约束/取值语义,如 `*_MS`、`ge=0`、「本街已投入」。
  - 文件开头不写解释模块职责/不变量的整段 docstring——设计依据放 `docs/`。
  - 例外:取值即含义的自文档化值枚举(如 `CardRank` 的 `"2".."9"`),在枚举上方一行说明取值编码即可;例外不适用于缩写型取值,花色 `h/d/c/s` 仍要逐成员标。
- **代码用了非文档的结构,必须当场改文档(双向同步)**:实现一旦采用与设计文档不同的签名/字段/结构/命名,就在同一次改动里同步对应设计文档([refactor/README.md](refactor/README.md) §0)。文档与代码不一致是缺陷,不是待办。
- **改动涉及前端可见的行为/契约,同次必须同步 [frontend/BACKEND_GUIDE.md](../../frontend/BACKEND_GUIDE.md)**(用户指示,0070 起)。覆盖协议形状、连接/重连语义、错误码/关闭码、加密层细节等;它是前端的入口契约文档,漂移等同协议漂移。
- **设计文档讲设计,不堆 Python**:`docs/` 用散文 + 表格 + 极简伪码,说清「为什么、不变量、边界」;精确字段清单与签名以代码为准(代码已有中文字段注释)。文档引用代码而非复制,需要示意时给几行最小伪码。
- **不过度解耦**。判据只有一个:会不会阻塞事件循环。远端可能卡住的 IO 才走队列 + 协程,例如 `ws.send` 遇慢客户端、DB commit,对应 Sender / PersistWriter;本地快操作直接串行写,例如写日志、Timer 的瞬时 dict 写,单线程 asyncio 下同步调用本就原子。反例与正解见 [log.md](log.md) / [timer.md](timer.md)。
- **错误信息可定位**:`Err` 的 `detail` 要带上下文(谁、哪个房间、什么状态),别只写一句 "invalid"。

## 提交前自检

> 下面是硬规则速查。push 前还须对照 [review.md](review.md) 做完整对抗式复审(七维),结论写进 [changes/](refactor/changes/) 的 `NNNN`「自 review」段——测试全绿不等于可提交。

- [ ] core 里没有 `await`/IO/`import fastapi|sqlalchemy`?
- [ ] 改动的 `reduce` 只改工作副本,失败 `return [], Err(...)`,没用异常/事件表达错误?
- [ ] 没有在产出 event 后又改它引用的对象?
- [ ] 持久化走 `Persist` + delayDB,没有在 core 里 `await` DB、没有新加行锁?
- [ ] 没有新的裸字面量?该进配置的进了 [config.md](config.md) 的 settings?
- [ ] 没留 `.bak`/注释代码/调试输出?
- [ ] 每个新增 dataclass 字段 / 枚举成员都标了含义注释?没在文件开头堆复述文档的 docstring?
- [ ] 这次实现若偏离了设计文档(签名/字段/结构),对应设计文档已在同一次改动里同步?
- [ ] 新增/改动配置项时,`poker.env` 与 `*.example` 都同步了?

## 关于配置分模块(前瞻)

`.env` 与 settings 日后会按模块拆,例如 `TimerConfig` / `TableConfig` / `DBConfig`。拆分不改变规则:业务代码始终引用对应 settings 对象,绝不内联字面量;新增模块照 [config.md](config.md) 建子配置。
