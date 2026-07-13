# 编码规范(总纲 · 先读我)

> 任何在本项目(`service/`)写代码的人或 AI,**动手前先读完这一篇**。它是所有约定的入口;每条规则都链到落地细节。违反「硬规则」的改动一律不接受。

## 阅读顺序

1. 本篇 —— 全部规则一览
2. [architecture.md](architecture.md) —— 分层、并发模型、不变量(为什么有这些规则)
3. [storage.md](storage.md) —— 内存权威 + 工作副本回滚 + delayDB(状态怎么活、怎么回滚、怎么落库)
4. [core.md](core.md) —— 游戏状态机:域模型、命令全集、一手牌的 reduce;细则见 [rules.md](rules.md)(盲注/下注轮关闭/边池 + 测试)
5. 按你要改的模块再读:[connection.md](connection.md)(连接管理与 shell 装配) · [lobby.md](lobby.md)(大厅与房间生命周期) · [messaging.md](messaging.md)(房聊与私信) · [presence.md](presence.md)(在线状态) · [rest.md](rest.md)(排行榜/历史/资料) · [wire.md](wire.md)(协议契约) · [models.md](models.md)(域/wire/DB 三套表示) · [timer.md](timer.md) · [error.md](error.md) · [config.md](config.md) · [log.md](log.md) · [user.md](user.md) · [db.md](db.md) · [auth.md](auth.md)
6. 工程:[dev.md](dev.md)(Poetry/Alembic/环境) · [db-migrations.md](db-migrations.md)(Alembic 迁移用法) · [testing.md](testing.md)(测试策略) · [review.md](review.md)(提交前复审 —— push 前必走)

## 核心模型(一句话)

**内存权威 + 单写者 + 工作副本回滚。** `world`(全部房间 + 全局用户积分)是内存权威;唯一的 GameLoop 串行处理命令:每条命令先把工作集(目标房间 + users 表)深拷贝成**工作副本**,`reduce` 只改副本,**成功 commit 回 `world`、失败/异常整份丢弃**。需要落库的数据走统一的 **delayDB** 通道异步追平 DB。

## 硬规则(违反即打回)

逐条对应 [architecture.md](architecture.md) 的不变量:

1. **分层不可逆**:`core`(游戏规则)纯同步——禁止 `async`/`await`/IO/DB/`sleep`/读墙钟;IO 只在 `shell`。core 不 import 任何 FastAPI/SQLAlchemy/WebSocket 符号。
2. **`reduce` 原子**:处理一条命令期间不 `await`;只改 GameLoop 给的**工作副本**,**成功才 commit、失败/异常整份丢弃** ⇒ `world` 要么全改、要么一字节不动。失败安全由「丢弃副本」保证,**不再强制先校验后改**(但仍建议校验前置以求清晰)。详见 [error.md](error.md)。
3. **事件不持有会被改写的引用**:跨命令隔离由工作副本 commit 天然保证(提交即替换引用);唯一纪律是**同一条 reduce 内产出 event 后别再改它引用的对象**(事件一般在末尾构造,自然满足)。
4. **错误是返回值(`Err`),不是事件、不用异常做控制流**:`reduce -> (events, Err | None)`,成功与失败互斥;业务校验失败 `return [], Err(code, detail)`,由 GameLoop 回发命令发起人(`origin`);core helper 之间也用 Go 风格 `Err | None` / `(value, Err)`,绝不 `raise`。异常只留给真正的 bug。详见 [error.md](error.md)。
5. **定时器/连接/断线一律转 `Command` 进 `inbox`**:不在后台 task 里直接改 `world`、不旁路 `ws.send`。详见 [timer.md](timer.md)。
6. **对外发送只经 per-connection Sender 队列**:禁止 `create_task(ws.send())` 或别处直接 `ws.send()`(破坏保序/阻塞)。
7. **持久化只走「内存权威 + delayDB」**:需落库的数据从 DB 读一次进内存、改内存、产出 `Persist` 异步落库;**载入决策在 reduce、shell 不读 `world`**;唯一写者 ⇒ 无行锁 / `with_for_update`。详见 [storage.md](storage.md)(模型)/ [db.md](db.md)(写通道)/ [user.md](user.md)。
8. **每条命令只作用于一个房间**:工作副本与回滚据此界定;跨房间操作需要时另议。**一个用户也只在一个房间**(`UserState.room`,`Connect` 别房即拒),保证全局积分驱逐无歧义。详见 [user.md](user.md)。
9. **不硬编码可调参数**:超时、盲注/买入上下限、tick、flush 周期等必须进配置,代码只引用 `gameconfig.XXX`。详见 [config.md](config.md)。

## 通用规范

- **无魔法数字**:裸字面量(`15`、`0.5`、`1000`)不进业务逻辑。可调的进配置;真常量写成具名常量(`HOLE_CARD_COUNT = 2`),名字/注释标单位(`*_MS`、`*_SECONDS`)。
- **类型标注齐全**:函数签名、模型字段都带类型;有结构的数据用 Pydantic/dataclass,别用裸 dict/tuple。
- **命名表意**:避免 `data`/`tmp`/`x`;布尔用 `is_`/`has_` 前缀。
- **不留死代码**:不提交 `.bak`、注释掉的大段代码、调试 `print`。(原型 `app/auth/services.py.bak` 这类死代码已随 [0027 原型拆除](refactor/changes/0027-prototype-teardown.md)一并清理。)
- **风格随邻里**:新代码的命名、注释密度、缩进、idiom 与所在文件保持一致。
- **注释讲「为什么」**:解释意图与不变量,而非复述代码;边池、超时 staleness 这类反直觉处必须有注释。
- **注释一律用中文**:代码注释、docstring 都用中文(与设计文档同语言);只有**标识符**(类/函数/变量名)和**提交信息**用英文(见 [dev.md](dev.md))。
- **字段必须标注含义,模块别写复述文档的大段 docstring**:每个 `dataclass`/Pydantic 字段、每个枚举(`Enum`/`StrEnum`)成员都用**简短行内中文注释**注明「这是什么」(含单位/约束/取值语义,如 `*_MS`、`ge=0`、「本街已投入」)——这是字段级文档,不是复述逻辑。但**不在文件开头写整段解释模块职责/不变量的 docstring**:那些设计依据放 `docs/` 的设计文档,代码里只放字段含义与极少数非显然的「为什么」。两者别混。**例外**:取值即含义的自文档化值枚举(如 `CardRank` 的 `"2".."9"`),逐成员注释只会复述代码、反与上一条「注释讲为什么不复述」打架;这类枚举在**枚举上方一行**说明取值编码即可,缩写型取值(花色 `h/d/c/s`)仍逐成员标。
- **代码用了非文档的结构,必须当场改文档(双向同步)**:不止「文档对不上就改文档」([refactor/README.md](refactor/README.md) §0)——只要实现采用了与设计文档**不同的签名/字段/结构/命名**(哪怕只是更优的小改),就**必须在同一次改动里同步对应设计文档**,绝不允许留下「设计文档 ≠ 已落地代码」的不一致。文档与代码不一致是缺陷,不是待办。
- **变动涉及前端需要知道的行为/契约,同一次改动里必须同步 [frontend/BACKEND_GUIDE.md](../../frontend/BACKEND_GUIDE.md)**(用户指示,0070 起):协议形状、连接/重连语义、错误码/关闭码、加密层细节等前端可见面,改了后端就改前端手册——它是前端的入口契约文档,漂移等同协议漂移。
- **设计文档讲设计,不堆 Python**:设计文档(`docs/`)用**散文 + 表格 + 极简伪码**说清「为什么、不变量、边界」;**不要把成段的 Python 类定义/实现搬进文档**。精确的字段清单与签名以**代码**为准(代码已有中文字段注释,自带文档),文档**引用代码**而非复制——复制出来的 Python 既臃肿、又制造「文档↔代码」双份维护负担。需要示意时给几行最小伪码即可。
- **不过度解耦:只有「会卡住循环」的慢 IO 才走队列 + 协程,廉价本地操作直接串行执行。** 判据同硬规则 1——「会不会阻塞事件循环」。远端可能卡住的 IO(`ws.send` 遇慢客户端、DB commit)才解耦(Sender / PersistWriter 协程 + 队列);本地快操作(写日志、Timer 的瞬时 dict 写)单线程 asyncio 下同步调用本就原子,**直接串行写下去,不套队列、不单开协程**。反例与正解见 [log.md](log.md) / [timer.md](timer.md)。
- **错误信息可定位**:`Err` 的 `detail` 带上下文(谁、哪个房间、什么状态),别只甩一句 "invalid"。

## 提交前自检

> 下面是**硬规则速查**;push 前还须对照 [review.md](review.md) 做**完整对抗式复审**(分层/文档同步/文档一致/数据模型/规范/测试/账本 七维),结论记进 [changes/](refactor/changes/) 的 `NNNN`「自 review」段——**绿测不等于可提交**。

- [ ] core 里没有 `await`/IO/`import fastapi|sqlalchemy`?
- [ ] 改动的 `reduce` 只改工作副本,失败 `return [], Err(...)`,没用异常/事件表达错误?
- [ ] 没有在产出 event 后又改它引用的对象?
- [ ] 持久化走 `Persist` + delayDB,没有在 core 里 `await` DB、没有新加行锁?
- [ ] 没有新的裸字面量?该进配置的进了 [config.md](config.md) 的 settings?
- [ ] 没留 `.bak`/注释代码/调试输出?
- [ ] 每个新增 dataclass 字段 / 枚举成员都标了含义注释?没在文件开头堆复述文档的 docstring?
- [ ] 这次实现若偏离了设计文档(签名/字段/结构),**对应设计文档已在同一次改动里同步**?
- [ ] 新增/改动配置项时,`poker.env` 与 `*.example` 都同步了?

## 关于配置分模块(前瞻)

`.env` 与 settings 日后会**按模块拆**(`TimerConfig` / `TableConfig` / `DBConfig` 等)。**拆分不改变规则**:业务代码始终引用对应 settings 对象、绝不内联字面量。新增模块照 [config.md](config.md) 建子配置即可。
