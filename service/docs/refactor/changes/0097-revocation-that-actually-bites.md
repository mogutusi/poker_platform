# 0097 · 让会话吊销真的生效(BUG-8 / 0072·N5)

日期:2026-08-25 · 性质:**缺陷修复(鉴权 shell + REST + 前端)**· 触发:[BUGS.md](../BUGS.md) BUG-8——`SessionStore.revoke` 全仓零调用者,无登出端点、无吊销通道。

## 缺陷是什么(比登记的更糟)

登记说的是「`revoke` 没人调」。开工前逐条核实,发现**两件登记里没写的事**:

1. **就算有人调,它也挡不住已经连上的人。** `revoke` 只做 `self._by_id.pop(sid)`([session.py](../../../app/auth/session.py))。而一条活着的 ws 连接持有的是 `Session` **对象**和从它派生的 `SecureChannel`;每帧的兜底检查读的是 `conn.session.expires_at`([receiver.py](../../../app/shell/receiver.py) 收帧 / [sender.py](../../../app/shell/sender.py) 出站),**从不回头查会话表**。所以 pop 掉表项之后,那条连接照样收发——只挡住「以后再握手」和「以后的 REST」。而 BUG-8 要防的恰恰是「凭证已泄露、对方可能已经连着」。
2. **前端的「退出」是假的。** [lobby/page.tsx](../../../../frontend/src/app/lobby/page.tsx) 的 `handleLogout` 只 `endLocalState()`(断连接 + 清本地)加 `endSession()`(清本地会话),**一个字都没告诉服务器**。用户点了「退出」,服务器上那把 `session_token` 仍然有效到 `SESSION_TTL` 自然到期。

## 登记给的修法有两处要更正

BUGS.md 写的是「补吊销通道(登出端点,或建 `name→sessions` 索引供改密/reset 时撤销)」。

- **索引不该建。** 同一个类里的 `rename_nickname` 已经在做「按 `name` 线性扫 `_by_id`」——同样的形状、同样的规模(在线 ≤20,架构文档锁死的适用范围)。再建一份索引就是第二份事实源,`create`/`lookup` 惰性删/`prune`/`revoke` 四处都要维护它,漂一处就是「吊销了但没吊销干净」。按既有 idiom 扫一遍,一行都不用多维护。
- **`issue --reset` 那一半在架构上做不到。** [kuser_admin.py](../../../scripts/kuser_admin.py) 是**独立进程**,而会话表是服务器进程的内存 shell 态([auth.md](../../auth.md)§会话表:「进程重启即失效」)。CLI 没有任何办法伸进服务器内存去吊销。这不是「还没做」,是登记时没意识到的进程边界。K_user 泄露场景下,真正的处置只有「重启服务器」(重启即清空会话表),这件事得**如实写进文档**,不能让人以为 `--reset` 顺带把会话也清了。

## 打算怎么改

**核心一招:吊销 = 摘表项 + 就地把这个 `Session` 对象判死(`expires_at = 0`)。**

这样不需要任何新机制——[auth.md](../../auth.md) §会话过期 早就写着「活 ws 连接也强制(0070),收帧和出站各比对一次 `expires_at`,过期即关连接 4401」。把对象判死,那条既有的强制路径就会在**下一帧(任一方向)** 关掉连接。连「双向零流量的连接活到下次有活动」这个例外都原样继承,不用新写一条语义。

1. `SessionStore.revoke(session_id) -> bool`:pop + 判死对象;返回是否真的吊销了(便于端点区分幂等)。
2. `SessionStore.revoke_all_for_name(name, *, except_id=None) -> int`:按 `name` 线性扫(`rename_nickname` 同款),逐个 revoke;`except_id` 用来「留下我这一个」。
3. **`POST /user/logout`**:走信封,吊销发起方自己的会话,回 `{}`。错误分层照 [rest.md](../../rest.md):信封不过 → 401;信封过了就一定成功(幂等)。
4. **改密码吊销该账号的其它会话**([profile.py](../../../app/rest/profile.py)),保留当前这个。这要**翻掉 auth.md 现在写的「v1 不吊销其它会话」**,理由写在下面。
5. **前端「退出」真的调服务器**:`rest.ts` 加 `logout()`,`handleLogout` 先 best-effort 调它再清本地——网络失败不能卡住本地登出,否则断网就退不出去。

### 为什么翻掉「改密码不吊销其它会话」

`POST /user/password` **要求旧密码**(0064 定的第二因子:防止只偷到 `session_token` 的人改密码把真用户锁死)。所以能触发改密的必是知道旧密码的本人。既然如此,「我怀疑号被盗了,所以改密码」是这套系统里用户唯一的自救手段——**改完旧会话还活着,这个手段就等于没有**。保留当前会话是为了别把正在操作的人自己踢下线。

### 有意不做

- **管理员吊销通道 / 给 CLI 加 IPC**:见上,跨进程。本规模的处置是重启,写进文档。
- **`K_user` 轮换不吊销已建会话**:auth.md §K_user 轮换 明写「轮换不影响已建会话」,是有意设计(会话密钥派生自 `session_token`,与 `K_user` 无关),本批不动。

## 要动的文件(预期)

- `app/auth/session.py`(`revoke` 语义 + `revoke_all_for_name`)、`app/rest/logout.py`(新)、`app/rest/profile.py`(改密后吊销)、`app/shell/lifespan.py`(挂 router)
- 测试:`tests/auth/test_session.py`、`tests/rest/test_logout.py`(新)、`tests/rest/test_profile.py`(改密吊销)、`tests/shell/`(活连接被判死后下一帧即关)
- 前端:`src/transport/rest.ts`、`src/app/lobby/page.tsx`
- 文档:[auth.md](../../auth.md)(§会话表 revoke 语义、§密码存储 翻掉 v1 那句、K_user 泄露处置)、[rest.md](../../rest.md)(新端点 + 错误分层)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(前端可见:新端点 + 吊销后连接会被关)、[BUGS.md](../BUGS.md) 划掉 BUG-8、[TODO.md](../TODO.md) 划掉 0072·N5(**两处都要**)

## 实际改了什么

按计划落地,**有一处落点与打算不同**(见下)。

### 后端

- **`app/auth/session.py`**
  - `revoke(sid) -> bool`:pop 之外**就地把 `Session` 判死**(`expires_at = 0.0`);返回是否真吊销了一条(端点据此可分辨幂等,虽然本批没用上返回值,类型上不再是「说不清做没做」)。
  - `revoke_all_for_name(name, *, except_id=None) -> int`:线性扫 `_by_id`,逐个 `revoke`。
- **`app/rest/profile.py`**
  - 新 `POST /user/logout`:`open_request` → **先 `seal_response` 再 `revoke(req.sid)`**。顺序写死是有意的:seal 用的是会话密钥,虽然它本身不查 `expires_at`,但依赖那个巧合就等于把「客户端收不收得到确认」寄托在实现细节上。
  - `change_password` 成功写库之后 `revoke_all_for_name(session.name, except_id=req.sid)`,并落一条 INFO(只记条数,不记 name/token,守脱敏红线)。

**落点与打算不同的一处**:打算里写「新建 `app/rest/logout.py`」,实际把 `/user/logout` 放进了既有的 `make_profile_router`。理由:它与 `/user/me`、`/user/password` 同前缀、同依赖(只要 `session_store`),放进去**不需要在 `lifespan.create_app` 里新挂一个 router**;单开一个文件只为一个 8 行端点,反而多一处接线要维护。`profile.py` 本就装着两个工厂的多个账号类端点,这是随邻里。

### 前端

- `src/transport/rest.ts`:新 `logout()`;`changePassword` 的文档注释从「改完**不会**吊销其它会话」改实。
- `src/app/lobby/page.tsx`:`handleLogout` 先 `void logout().catch(...)` 再清本地。**顺序是必须的,不是风格**:`postSealed` 同步地读会话、取 seq、封好帧,之后才 `await fetch`;反过来写的话 `requireSession()` 会抛、被 `catch` 吞掉,「退出」又悄悄退回只清本地。这一条写进了代码注释。

### 文档

[auth.md](../../auth.md) 新增 §吊销(判死语义 + 两个消费者 + 为什么不建索引 + 三条边界 + **`K_user` 泄露够不着的那块**)、§密码存储 翻掉「v1 不吊销其它会话」、§会话过期 记档「登出须重输 K_user」与前端现状的分歧;[rest.md](../../rest.md) 新增 `/user/logout` 节 + 改密那条改实(**连带纠正它附带的错误前提**「撤销需要 name→sessions 索引」)+ 节首「三个端点」改四个;[wire-protocol-guide.md](../../wire-protocol-guide.md) §10 REST 表补一行;[BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 端点表 + 「登出必须调服务器」+「会话被吊销之后 ws 收 4401」+ 关闭码表 4401 行;[frontend/docs/transport.md](../../../../frontend/docs/transport.md) §五 信封端点清单;[BUGS.md](../BUGS.md) 划掉 BUG-8 + 已修复表;[TODO.md](../TODO.md) 划掉 0072·N5。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **774 passed**(763 → 774,新增 11) |
| 前端 `tsc --noEmit` | 通过 |
| 前端 vitest | 90 passed |
| 浏览器 `npm run test:e2e` | 16 passed |
| 三条冒烟 | 全部通过 |
| 改完按 pid 杀 uvicorn、确认端口释放、重启并 grep 日志无 `address already in use` | 是 |

**对真后端的端到端实证**(不只是单测):写了一个用冒烟客户端自己的加密代码打真服务器的脚本,四条全过——

1. 登出前 `/user/me` 正常;2. `POST /user/logout` 回 `{status: ok}`;3. **同一 sid 再调 → 401**;
4. 同账号两个会话,A 改密 → **A 自己仍可用、B 被吊销(401)**、别的账号不受影响。

**反向变异验证 4 处**:

| 变异 | 变红的 |
|---|---|
| `revoke` 不判死对象(只 pop,退回 BUG-8 的结构性缺口)| `test_logout_revokes_the_calling_session` + `test_revoked_session_closes_the_live_connection` |
| `revoke_all_for_name` 忽略 `except_id`(改密把自己也踢了)| `test_revoke_all_for_name_spares_current_and_other_accounts` + `test_password_change_revokes_other_sessions_but_keeps_current` |
| 改密不吊销其它会话(退回 v1 行为)| `test_password_change_revokes_other_sessions_but_keeps_current` |
| `logout` 不吊销(端点变空操作)| `test_second_logout_with_same_sid_is_401` + `test_logout_only_kills_its_own_session` |

## 自 review

按 [review.md](../../review.md) 七维。本批是**安全面 + 前端可见契约**改动,最高风险面是「吊销是不是真的关严了」与「翻掉一条既有语义之后,还有谁在按旧语义说话」。

方法上仍用并行多 agent 对抗式复审(四组维度 → 每条发现两个独立视角试图证伪,一票反驳即不算确认)。**这一轮抓得比上一批狠:一个我完全漏掉的界面、三处文档自相矛盾、三个能活下来的变异,外加我自己写的一条假测试。** 逐条如下。

### 复审抓到并已修的

1. **(最严重)`settings/page.tsx` 是唯一调 `changePassword` 的界面,而它两处都在说反话。** 注释写着「后端明确『改密码不吊销其它会话』(rest.md §用户资料)」——它引用的正是**本批删掉的那句**;表单上方的静态说明更直接:「改完其它设备的登录状态不受影响。」这是**用户提交前就看得到的假话**。而实际后果是对面设备被 4401 踢回登录页、只看到一句「会话已过期」,对不上因果。已把注释、成功提示、静态说明三处一起改实。**这条最值得记**:我改了 `rest.ts` 里同一句陈述的 JSDoc,却没往上再走一层到真正给人看的地方。
2. **`rest.md` §用户资料 自相矛盾**:开篇「三个带身份的端点」+ 三行代码块,而我在同一节下面加了第四个。已改成四个并补进代码块。
3. **`frontend/docs/transport.md` §五 漏了新端点**:它是穷举形式的「每一个走信封的端点」清单,而 `rest.ts` 的文件头正指着这一节当契约。已补。
4. **`BACKEND_GUIDE.md` 关闭码权威表**(0087 定的那张)里 4401 一行仍只写「握手失败 / 撞上 exp」,而同一文件本批新增的段落已宣布吊销也走 4401。查表的人看不到新语义。已补第三种来源。
5. **`auth.md` 那句「主动登出后须重新手输 `K_user`」早被前端证伪**(`clearKUser()` 至今零调用者,K_user 留在 localStorage)。本批既然把「登出」从假动作变成真语义、又大改了会话章节,就不该继续放着一句假话。已就地记档为**未定案的分歧**(共享机器安全 vs 每周轮换的手输摩擦),并注明在定案前别当契约——**不擅自改前端行为**:那是 0074·B 的教训(前端那个取舍是有意的、注释里写着理由)。
6. **`actions.ts` 的 `endLocalState` 文档注释仍自称「登出」**,而本批的论点恰恰是「只清本地不算登出」。已改成「登出的本地那一半」,并写明另一半要调 `rest.logout()`。
7. **变更记录漏列 `wire-protocol-guide.md`**(它确实在本批 diff 里)。已补——账本与 diff 不符是 0093 的同款病。

### 复审抓到的三个「变异能活下来」,以及处置

| 变异 | 处置 |
|---|---|
| `revoke` 只判死、不摘表项 | **补测**:`test_revoke_kills_the_held_session_object` 加 `len(store) == 0`。文档把吊销定义成「摘表项 + 判死」两件事,只钉一件就是半个契约。 |
| 改密的吊销提到 `await update_password_hash` **之前** | **补测** `test_db_write_failure_revokes_nothing`:写库失败 → 500 且其它会话**不得**被吊销。否则「密码没改成、其它设备全掉线」,自救不成还挨一刀。 |
| `/user/logout` 里 seal 与 revoke **换序** | **不补测,如实记为等价变异**:`seal_response` 当前不查 `expires_at`,所以两种顺序今天行为完全相同,任何测试都钉不住它。这个顺序是**防御性**的(不依赖那个巧合),代码注释与 rest.md 都是这么写的,不是在描述一个可观测行为。硬编一个测试假装钉住了,比承认它没被钉住更糟。 |

### 我自己写的一条假测试(复审前自己发现)

`test_db_write_failure_revokes_nothing` 第一版用 `_raising_sessionmaker`(照抄同文件既有的「DB 错 500」用例)。但那个桩在**第一次**调用就抛,而第一次调用是更早的 `load_password_for_change`——端点在**查询**阶段就 500 了,根本走不到写库,更走不到吊销。于是无论吊销放在写库前还是后,它都绿。改成 `monkeypatch` 掉 `app.rest.profile.update_password_hash` 之后,变异才如期变红。**这正是 0092 记过的那类病**:测试名字说的是 A,实际验的是 B,而它一直绿着。

### 逐维

- **① 分层 / 不变量**:core 一行未动;`world` 一个字节没碰。会话态仍只活在 shell(auth.md 的硬要求)。没有新增对外 IO 通道、没有绕过 Sender 队列、没有 `ws.send`。**await 窗口专门查过**(这仓被 0074·C/F 咬过四次):`revoke_all_for_name` 与 `revoke` 全程同步,改密流程里它排在**所有 await 之后**,与改昵称那套三处联动的形状一致;单线程 asyncio 下不存在「读到半改」。
- **② 代码↔文档同步**:本批的重头,见上 1–7。协议面新增一个 REST 端点 ⇒ 四处前端可见文档全部同步(`BACKEND_GUIDE.md`、`wire-protocol-guide.md` §10、`rest.md`、`frontend/docs/transport.md`)。ws 协议与 codegen 不受影响(REST 不进 `wire.gen.ts`,如实核过)。
- **③ 文档↔文档一致**:BUG-8 在 **BUGS.md 与 TODO.md 两处**同批划掉(0093 的教训),摘要口径一致且都写明「登记的索引方案不需要 + `--reset` 那半架构上做不到」。本批所有改动文档的相对链接扫过,**0 条死链**。
- **④ 数据模型正确性**:`expires_at = 0.0` 是**墓碑**,不是普通过期值——已在字段注释里点明(此前只写在 `revoke()` 函数体和 auth.md 里,而另外两个模块拿这个字段做鉴权判据)。核过**没有复活路径**:全仓写 `expires_at` 的只有两处(构造、`revoke`),`create` 造的是全新对象 + 全新 sid,被吊销的会话永远回不来。`revoke -> bool` / `revoke_all_for_name -> int` 目前无生产消费者,与同类 `rename_nickname -> int` 同形,是既有 idiom。
- **⑤ 规范合规**:注释中文、讲「为什么」(尤其三处反直觉点:为什么摘表项不够、为什么不建索引、为什么前端的调用顺序是必须的);无魔法数(`0.0` 是语义值,已注释);无死代码;测试文件归位——误放在 `test_change_password.py` 的 logout 路由注册测试已挪回 `test_logout.py`。
- **⑥ 测试充分**:**7 处反向变异确认**(首轮 4 + 复审补的 3 中有 2 可钉)。新增测试都对着**派生关系或可观测后果**:改密那条断言「B 被吊销 **且** A 仍可用 **且** 别人不受影响」,而不是只看一个布尔。**另有对真后端的端到端实证**(登出后同 sid 必 401;改密后 A 活 B 死),不只是单测。**缺口如实记**:(a) seal/revoke 换序是等价变异,钉不住(见上);(b) 「吊销 → 活 ws 被 4401 关掉」在**浏览器**里没有用例,只有 shell 层的 `test_revoked_session_closes_the_live_connection`;(c) 前端 `handleLogout` 本身没有组件级测试,我钉的是它依赖的那条传输层顺序性质(`rest.test.ts`,变异验证过)。
- **⑦ 流程账本**:变更记录先行,收工回填,**「打算 ↔ 实际」的偏离明写**(端点落在 `profile.py` 而非新建 `logout.py`,理由是不必新挂 router)。复审的确认项当场修、驳回项与等价变异如实记。提交信息英文、引用 0097。

### 有意没做,留档

- **偷的若正是你手上这把 token(同一 `session_id`),改密赶不走他**:`except_id` 放过的是「当前这个会话」,而他与你共用它。这是「保留当前会话」的直接代价,与主流做法一致;补救是**改完密码再登出一次**再重新登录。已写进 [auth.md](../../auth.md) §吊销。要不要改成「改密即全踢(含自己)」是一个 UX/安全取舍,**该由用户拍板**,不在本批擅动。
- **吊销不是即时屏障**:已经进门的那一帧照常执行;零流量的连接活到下次有活动(继承 exp 兜底的既有例外),期间它在 `world` 里仍占座、presence 仍报在线。三条边界都已写进 auth.md,不留成隐性缺口。
- **`clearKUser()` 仍零调用者**:见上 5,是未定案的分歧,记档不擅改。
- **4401 之后前端不调 `endSession()`**(`actions.ts` 的 auth-lost 分支只清房间/私聊并跳转,`getSession()` 仍返回那个已死会话):**先于本批存在**,但本批让 4401 成了活跃用户会真撞上的主路径。属于前端会话生命周期的独立一批,记档不顺手改。
