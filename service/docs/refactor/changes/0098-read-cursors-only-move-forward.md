# 0098 · 已读游标只许前进(BUG-11 / 0072·N-e9)

日期:2026-08-25 · 性质:**缺陷修复(持久化写路径)**· 触发:[BUGS.md](../BUGS.md) BUG-11「DM 读游标无单调防护,游标可能被旧值回拨」。

## 缺陷是什么

`DMMarkRead.read_through` 是**客户端回传的时间戳**,一路原样进 `DMReadCursorWrite`,再由 `OrmPersister._upsert_dm_cursor` **无条件覆盖**已有行([orm_persister.py](../../../app/db/orm_persister.py) 的 `existing.read_through_ts = ts`)。服务器从不比较新旧,于是游标可以被写小。

游标是「我读到几时为止」的水位,[messaging.md](../../messaging.md) 里**一表三用**,回拨会同时污染三处:

| 用途 | 回拨之后 |
|---|---|
| 未读判据 `created_at > 游标`(`load_unread_dms`) | 已读过的私信重新变未读,下次(重)连当作新消息再推一遍 |
| 发件人已读回执(查 `peer=我` 的行) | 对面看到「他把已读又变回未读了」 |
| 保留清理 `read_through_ts >= created_at`([0041](0041-dm-retention-cleanup.md)) | 本已可删的行重新变得不可删,赖在库里 |

## 顺带查出的另一头:游标可以指向未来

登记只说了「回拨」。**同一处缺口还有反方向的一半,而且后果更硬**:`read_through` 没有上界,客户端可以送一个远期时间戳。之后

- `load_unread_dms` 认为「什么都读过了」,**此后到达的私信永远不会在登录补收里出现**;
- 过了保留期,`cleanup_dms` 判定它们「已读且过期」→ **真的删掉**。用户从没看见过这些消息,它们就没了。

这不是「回拨」的对称面,是**数据丢失**,所以一并修。判据现成:`created_at` 由 shell 盖墙钟([messaging.md](../../messaging.md)「时间游标而非自增 id」),所以任何合法游标都**不可能超过服务器此刻**——客户端回传的值源自它收到的 `DMDelivered.created_at`,那个值本身就是服务器盖的。

**严重度仍按 low 处理**:`reader` 取连接 nick、不信报文,所以一个用户只能糟蹋自己的收件箱,伤不到别人;唯一的跨用户面是发给对端的 `DMRead` 回执(咨询性质)。这与 [auth.md](../../auth.md) 威胁模型「不防已拿到密钥的内部人」一致,但**「自己能把自己的私信弄丢」不该是设计的一部分**。

## 修在哪一层(先读设计文档)

- [db.md](../../db.md) / [storage.md](../../storage.md):`PersistWriter` 是**唯一 DB 写者**,状态写按键覆盖。
- `_upsert_dm_cursor` 本来就是「SELECT-by-PK → 无则 INSERT、有则改」,**已经在事务里读到了旧值**([changes/0039](0039-dm-read-cursor.md) 选它是为了 race-free 且跨方言)。

所以单调性钳在**持久化写**这一层最省:唯一写者 + 已在手的旧值 + 同一事务 ⇒ 天然 race-free,不用加查询,也不碰方言差异(`GREATEST` 在 sqlite 没有,`max(a,b)` 在 pg 是聚合——写 SQL 反而会踩方言坑)。

**不修在路由层**:`route_dm_mark_read` 要判单调就得先读 DB,而它读到的是**可能已被写缓冲超越的旧值**(delayDB 是异步追平的),两次快速标读还会互相穿越。上界(不许指向未来)则相反,必须在路由层钳——那里才有 shell 墙钟,而 core/持久化层不读钟。

## 打算怎么改

1. `OrmPersister._upsert_dm_cursor`:`existing.read_through_ts = max(existing.read_through_ts, ts)`,即只前进不后退;写进注释说明为什么(三处用途 + 唯一写者)。
2. `route_dm_mark_read`:把 `read_through` 钳到不超过 shell 此刻的墙钟。时钟从调用方注入(同 `now` 可注入的既有做法),不在函数里硬读。
3. 文档:[messaging.md](../../messaging.md) §游标 写明「只前进、不超过服务器此刻」这条不变量;[BUGS.md](../BUGS.md) 划掉 BUG-11、[TODO.md](../TODO.md) 划掉 N-e9(**两处都要**)。

协议面不变(`DMMarkRead` 字段不动),所以 codegen 与 [BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 不动——但客户端可见的**行为**变了(送一个回拨或未来的游标不再被采纳),这一条要在 BACKEND_GUIDE 里说一句。

## 实际改了什么

按计划落地,**外加一处计划里没有、但不做就会把事情弄得更糟的修**(见「差点亲手制造一个更严重的缺陷」)。

- **`app/db/orm_persister.py`** `_upsert_dm_cursor`:`existing.read_through_ts = ts` → 只在新值更晚时才写。
- **`app/shell/messaging.py`** `route_dm_mark_read`:把 `read_through` 钳到 shell 此刻;**回执 `DMRead` 报的是采纳后的值**,不是客户端自报的(否则对面看到的进度与库里不一致)。时钟按邻里写法直接 `datetime.now(timezone.utc)`(同 `route_direct_message`),不另加注入参数。
- **`app/db/queries.py`** 的既有 `_as_utc` **提升为公开 `as_utc`**,三个调用方共用(读侧原有两处 + 本批新增两处)。第一版我在 `dm_records.py` 里另写了一份——那就是 N-e36「二份事实源」的同款,自 review 抓出后删掉,用先存在、注释更完整的那个。

**与「打算」的两处出入**:

1. 打算说时钟「从调用方注入」,实际按邻里写法直接读(`route_direct_message` 就在同一文件里这么盖 `created_at`)。多一个参数只为测试可控,而这里的测试用「相对 now」的断言就够,不必给生产签名加负担。
2. 打算说客户端可见行为的说明放 [BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md),实际**两处都写了**:BACKEND_GUIDE §5 的「别自己推」清单(前端入口契约)+ [wire-protocol-guide.md](../../wire-protocol-guide.md) §3 的 `dm_mark_read` 条目(展开版)。后者是自 review 提醒才补的——前者指着后者当展开版,只写一处就会对不上。

### 差点亲手制造一个更严重的缺陷

第一版把单调性写成 `max(existing.read_through_ts, ts)`。**这行会炸。** 游标列是 `DateTime(timezone=True)`:pg 上带 tz 回来,**sqlite 上读回丢 tz**;而客户端回传的值是 aware。naive 与 aware 比大小 → `TypeError`,抛在**唯一 DB 写者**的事务里 ⇒ 整批状态写回滚 → `PersistWriter` 回灌重试 → 再抛,**永远追不平**。换句话说,为修一个 low 级缺陷,险些换来一个「积分与私信全都落不了库」的 high 级故障。

发现方式不是推理而是**实跑**:写完先拿真 sqlite 复放了一遍写-读-再写,当场看到 `tzinfo=None` 和那句 `TypeError`。事后确认**既有测试也挡得住**(`test_dm_cursor_updates_when_present` 会红),但当时我还没跑到它——这条记下来是想说:`DateTime` 列的 tz 在两个方言上不一致,这仓已经吃过一次亏(`K_user` 的 `*_until` 因此改存 epoch 秒,见 [auth.md](../../auth.md)),**下一处比较 `DateTime` 列的地方还会踩**。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **779 passed**(775 → 779,新增 4) |
| 前端 vitest / `tsc` | 93 passed / 通过(未改前端) |
| 浏览器 `npm run test:e2e` | 16 passed |
| 三条冒烟 | 全部通过 |
| 改完按 pid 杀 uvicorn、确认端口释放、重启并 grep 日志无 `address already in use` | 是 |

**反向变异验证 5 处**:

| 变异 | 变红的 |
|---|---|
| 游标可回拨(退回 BUG-11) | `test_dm_cursor_never_moves_backwards` |
| 不钳未来游标 | `test_dm_mark_read_clamps_future_cursor` |
| `as_utc` 退化成恒等(naive/aware 混比) | `test_dm_mark_read_normalises_client_timestamp_to_utc` + `test_dm_cursor_never_moves_backwards` + **既有的** `test_dm_cursor_updates_when_present` |
| 只归一比较、落库存原样(自 review 抓到的 high 绕过) | 上面那条 + `test_dm_mark_read_offset_timestamp_cannot_become_future_cursor` |
| 只 `astimezone` 不 `as_utc`(naive 被当本地时区) | `test_dm_mark_read_normalises_client_timestamp_to_utc` |

第三条变异的价值在于:它证明 tz 归一**不是可有可无的防御**,去掉就真的红——而且连既有用例都红,说明这条路径本来就走得到。

## 自 review

按 [review.md](../../review.md) 七维。本批动的是**持久化写路径 + 客户端可控输入**,最高风险面是「钳位是不是真的钳住了」。

同样用并行多 agent 对抗式复审(三组维度 → 每条两个独立视角证伪)。**这一轮抓到的最重一条是我自己修出来的 high**——比原缺陷还严重。

### 复审抓到的 high:钳位可以用非 UTC 偏移绕过(我自己引入的)

第一版写的是「归一后**比较**,但落库存客户端**原样**的值」。这在带偏移的输入上直接失效:

1. 客户端送 `2026-08-25T21:46:54+08:00` —— 绝对时刻是 13:46Z,**确实在过去**,钳位放行;
2. sqlite 落 `DateTime(timezone=True)` 时**丢掉 tz 标签、只存墙钟数字** → 库里是 `21:46:54`;
3. 读回是 naive,`as_utc` 按 UTC 解读 → **21:46Z,比 now 晚 8 小时**。

于是「未来游标」原样成立:此后到达的私信永不进登录补收,过保留期被 `cleanup_dms` 当「已读且过期」真删。**我声称关掉的那扇门,自己从旁边推开了。** 实测复现过(真 sqlite 往返,见下)。

修法:**存的必须就是比过的那个值** —— `min(as_utc(x).astimezone(timezone.utc), now)`。两步都不能省:`as_utc` 只给 naive 补标签,`astimezone` 才把别的偏移真正换算到 UTC。

**这里还有一层教训**:我第一次改只写了 `min(as_utc(x), now)`,自以为修好了——是**新写的那条回归测试当场变红**才发现 `as_utc` 对 aware 值是恒等的。测试比我先想明白。

### 复审抓到并已修的其余各条

- **`as_utc` 是第二份事实源**:`app/db/queries.py` 早有一个逐字等价的 `_as_utc`,连注释解释的坑都是同一个。本仓把这种形状登记成缺陷处理过(N-e36)。已删我那份,提升既有的为公开。
- **四处「后写覆盖前写」口径已成假话**:`db/models.py` 字段注释、`db/dm_records.py` 类注释、`shell/persist.py` 分流注释、`docs/db.md` 两处(两类写表格 + 载荷伪码)。游标现在是状态写里**唯一**一个不「后写必覆盖」的键,四处逐一改实。
- **`wire-protocol-guide.md` §3 `dm_mark_read`** 没说新语义(复审进行中我已补:回拨不采纳、未来被钳、`dm_read` 回执可能小于你送的值)。

### 逐维

- **① 分层 / 不变量**:core 一行未动。钳位在 shell(唯一有墙钟的层),单调性在唯一 DB 写者的事务里(旧值已在手 ⇒ race-free)。`app/db` 仍不 import shell。`route_dm_mark_read` 的 `put` 是同步的,不破「不在 await 后改状态」。
- **② 代码↔文档同步**:[messaging.md](../../messaging.md) 新增不变量小节 + 把「同 flush 窗内回拨」如实记进既有的「崩溃/竞态(接受)」;[db.md](../../db.md) 两处、三处代码注释改实;[wire-protocol-guide.md](../../wire-protocol-guide.md) 补客户端可见语义。
- **③ 文档↔文档一致**:BUG-11 在 **BUGS.md 与 TODO.md 两处**划掉(0093 教训),两边都写明「登记只说了一半」。链接扫过 0 死链。
- **④ 数据模型正确性**:游标的时间域现在只有一种表示(落库前一律归一到 aware UTC),消掉了「同一列里混着 naive 与各种偏移」这种可表达的坏状态。`min`/`>` 的边界都核过:恰好等于 `now` 采纳、相等时不重写。
- **⑤ 规范合规**:注释讲「为什么」(尤其两处反直觉:为什么两步归一都不能省、为什么单调性不放路由层);无魔法数;无死代码(删掉了重复 helper);测试用本文件既有的 `_naive` idiom,不另 import app 侧归一函数。
- **⑥ 测试充分**:**5 处反向变异确认**(游标可回拨 / 不钳未来 / `as_utc` 恒等 / 只比不存 / 只 `astimezone` 不 `as_utc`)。新测试都断言**性质**而非字面量:单调那条断言「停在两次写里较晚的那个」,偏移那条断言「去掉 tz 标签后仍不在未来」——后者正是 sqlite 落库真正会做的事,所以它测的是真实后果而不是内存里的对象。**缺口如实记**:(a) 同一 flush 窗内的回拨仍会生效(见下,已论证为保守窗口);(b) 没有跨 pg 的验证,而 tz 行为恰恰是两个方言分叉的地方——本机只有 sqlite,pg 侧靠 `DateTime(timezone=True)` 语义推理,[db-migrations.md](../../db-migrations.md) §3 要求的「新迁移在 pg 复验」不覆盖纯代码改动。
- **⑦ 流程账本**:变更记录先行、收工回填;计划外的两处(tz 归一、helper 去重)明写来由。提交信息英文、引用 0098。

### 有意没做,留档

- **同一 flush 窗内的回拨仍会生效**:`WriteBuffer` 状态写按键**后写覆盖**,单调守卫只看得到库里的旧值。**后果是保守的**——游标偏小只会让那几条下次补收时重发,`cleanup_dms` 要求「游标 ≥ created_at」,游标低只会更不敢删,**不会丢**。要堵它得让路由去读缓冲里的待落值(把「只前进」复制到第二处),或给通用 `WriteBuffer` 加按类型的合并(破坏它单一语义)。两者都比这个窗口更糟。已写进 [messaging.md](../../messaging.md)「崩溃/竞态(接受)」第 3 条。
- **`DateTime` 列的 tz 在两个方言上不一致**,这仓已经吃过两次亏(`K_user` 的 `*_until` 因此改存 epoch 秒;本批这一处)。要不要把这类列统一改成存 epoch 秒、或在 engine 层统一归一,是一次跨模块的决定,值得单独议。
