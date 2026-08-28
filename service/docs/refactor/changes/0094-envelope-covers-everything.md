# 0094 · 把加密层改回全覆盖:登录是唯一暴露在外的入口

日期:2026-08-25 · 性质:**回归既定设计(安全面)**· 触发:用户指出现状与他的设计不符,原话:「国密加密那一层是全覆盖只有登陆可以暴露在外成为唯一入口的」。

## 这不是新决定,是把 0057 定的设计改回来

翻记录,链条很清楚:

| 时点 | 说了什么 |
|---|---|
| **[0057](0057-p5-unified-encrypted-channel-design.md) 定案** | 「登录后**一切流量**(ws + REST)走同一加密信封,解密即认证、无 JWT」 |
| [auth.md](../../auth.md) 至今 | §加密信道 标题仍是「登录后**一切流量**:ws 与 REST 同构」;§两层密钥仍写「会话密钥负责之后**一切流量**」 |
| **[0062](0062-p5-rest-envelope-user-me.md) 决策 5 收窄** | 「只有需身份的端点走信封,公开读(lobby/leaderboard/hands)**本砖留明文**」,理由是「三者无隐私」,并标 **决策(可改)**:日后要「一切 REST 加密」再收编 |
| 之后 | 没有人回来收编。P5 落完(0057–0066),三个读端点一直裸着 |

于是 [auth.md](../../auth.md) 同一篇里,标题说「一切流量」、覆盖面那条说「公开读留明文」,**自相矛盾**至今。

**0062 的理由不成立**,两点:

1. 「三者无隐私」把「没有底牌」当成了「没有隐私」。`/hands` 是**逐手财务流水**,还能 `?user=` 点名查任何人;攒起来是资金曲线加社交关系图。[auth.md](../../auth.md) 威胁模型第一条要防的正是「嗅探读消息」。
2. 它引用 [rest.md](../../rest.md) 的「历史 dev 无鉴权」当依据——而那句是**加密信道还不存在时**写的占位([0016](0016-replan-wire-first.md) 把 P5 推到最后,P7 读端点先落地,当时没有信封可套)。拿前置占位当事后依据是循环论证。

还有一层更根本的:**没有 TLS**。信封不只是内容隐私,它是这套架构里唯一的传输保护。留一个明文 GET,等于在「无 TLS」的前提下开了个洞。

## 打算怎么改

**三个读端点收编进信封,与 `/user/me` 同构**:`GET` → `POST`,查询参数进内层 JSON,响应用 `seal_response` 封回。收编后「解密即认证」自动生效:**未登录者拿不到任何数据**,登录是唯一暴露在外的入口。

- `GET /lobby/rooms` → `POST /lobby/rooms`,参数 `{}`,响应 `{"rooms": [...]}`
- `GET /leaderboard?limit=` → `POST /leaderboard`,参数 `{"limit"?: int}`,响应 `{"entries": [...]}`
- `GET /hands?user=&room=&before=&limit=` → `POST /hands`,同名参数进内层,响应 `{"hands": [...]}`

**为什么响应要包一层对象**:`seal_response` 的载荷是 `dict`,与请求侧「参数一律对象形」同一条规矩;裸数组还会堵死日后加分页元信息的路。

**授权范围本批不动**。收编只解决「传输裸奔 + 未登录可读」;「登录用户能不能查别人的历史」是另一个决定,用户尚未拍板,本批**保持现状**(`?user=` 仍可点名),并在 [rest.md](../../rest.md) 记成待定项。不顺手收紧——那会悄悄改掉前端历史页「全部」页签的行为。

## 要动的文件(预期)

- `app/rest/lobby.py` / `leaderboard.py` / `hands.py`(改 POST + 信封)、`app/shell/lifespan.py`(三个 router 现在要 `session_store`)
- 测试:`tests/rest/test_lobby.py` / `test_leaderboard.py` / `test_hands.py`(改走信封)+ 各补一条「无信封必 401」
- 前端:`src/transport/rest.ts`(三个函数改 `postSealed`)、大厅页/历史页的注释
- 冒烟:`scripts/smoke-client.mjs`(加 sealed 读助手)+ `smoke-e2e.mjs` / `smoke-raise-sidepot.mjs` 的 `/leaderboard` 读
- 文档:[auth.md](../../auth.md)(覆盖面改回全覆盖,消掉自相矛盾)、[rest.md](../../rest.md)(共同原则 3 + 三节)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(前端可见契约:公开读没了)、[frontend/docs/transport.md](../../../../frontend/docs/transport.md) §五

## 实际改了什么

三个读端点全部收编,按计划落地。

### 后端

- **`GET` → `POST` + 信封**,与 `/user/me` 同构:`open_request` 拆参数、`seal_response` 封结果。
  - `POST /lobby/rooms`:`{}` → `{"rooms": [...]}`
  - `POST /leaderboard`:`{"limit"?}` → `{"entries": [...]}`
  - `POST /hands`:`{room?, user?, before?, limit?}` → `{"hands": [...]}`
- **参数校验从框架挪进端点**。收编前 `limit`/`before` 的范围由 FastAPI 的 `Query(ge=, le=)` 兜;参数进了信封,框架就管不着了。所以补了 `_read_limit` / `_opt_str` / `_opt_positive_int`,越界或类型错回 **400** —— 信封已验过 ⇒ 是客户端 bug,不是鉴权问题(rest.md 的错误分层)。**特意不做「截断成合法值」**:那会让 `limit=0` 悄悄变成一整页,是最难查的那种「没报错但不对」。
- `hands` 的响应要 `model_dump(mode="json")`:信封内层是 `json.dumps`,原样丢 `datetime` 会 `TypeError`。
- 三个 router 现在都要 `session_store`,`create_app` 跟着改。

### 测试

- 抽 **`tests/rest/_sealed.py`**:`seal_req` / `open_resp` / `call`(一来一回并核对 seq 回显)。收编后有 6 个测试文件要封信封,这套 10 行再抄三份就太多了——而它直接压在协议面上(密钥分域、seq 回显、内层形状),抄一份多一处会漏改的地方。
- 三个端点各补两条:**「没有有效会话 → 401」**(0094 的正题)和**「参数畸形 → 400 而非默默截断」**。
- 路由表断言从 `"GET" in methods` 改成 `methods == {"POST"}` —— 只断言「有 POST」的话,明文 GET 若还留着也照样绿。

### 前端与冒烟

- `transport/rest.ts` 三个函数改走已有的 `postSealed`,并从响应里取 `rooms`/`entries`/`hands`。
- `scripts/smoke-client.mjs` 加 `restCall(session, path, params)`(REST 域密钥 + 自增 seq)。
- `smoke-e2e.mjs` 的积分基线原本在**登录之前**读排行榜——收编后读不到了,所以把两次登录提到基线之前。这是收编真正会绊到人的地方:**任何「先看一眼再登录」的流程都不再成立**。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **760 passed**(755 → 760,新增 5) |
| 前端 vitest | 90 passed |
| 浏览器 `npm run test:e2e` | 16 passed |
| 三条冒烟 | 全部通过(守恒 1920 → 1920;三人 3000) |
| `curl` 实测 | `GET /hands`、`GET /leaderboard`、`GET /lobby/rooms` 一律 **405**;无效信封 POST → **401** |

**反向变异验证 2 处**:

| 变异 | 变红的 |
|---|---|
| lobby 端点绕过 `open_request`(退回无鉴权) | `test_router_rejects_without_envelope` |
| `limit` 越界改成默默截断 | `test_route_bad_limit_is_400_not_silently_clamped` |

## 自 review

按 [review.md](../../review.md) 七维。本批是**安全面**改动,最高风险面是「是不是真的关严了」与「有没有把某条路径漏在外面」。

- **① 分层 / 不变量**:core 一行未动。三个端点的读取语义不变(`/lobby/rooms` 仍是唯一只读 committed world 的 REST,且仍全程无 await ⇒ 对 GameLoop 原子)。信封在 REST 边界,端点只见明文参数,与 ws 的分层一致。
- **② 代码↔文档同步**:这是本批的重头。[auth.md](../../auth.md) 覆盖面那条**改回全覆盖**并写明 0062 的理由为何不成立——同一篇里「一切流量」与「公开读留明文」自相矛盾了两个月,不消掉的话下一个人还会照后者办事。[rest.md](../../rest.md) 共同原则 3 + 三节;[wire-protocol-guide.md](../../wire-protocol-guide.md) §10 整表重写;[lobby.md](../../lobby.md) 伪码;[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) §6 整节 + §8 那句「直接 curl 可验」(现在不可验了);[frontend/docs/transport.md](../../../../frontend/docs/transport.md) §五;[frontend/docs/state.md](../../../../frontend/docs/state.md) 大厅表。
- **③ 文档↔文档一致**:[TODO.md](../TODO.md) 里 P7 那条的 `GET` 写法留作历史并加注指向本篇(那是当时的形状,不改写历史)。
- **④ 数据模型正确性**:响应统一包一层对象而不是裸数组——`seal_response` 的载荷是 `dict`,与请求侧「参数一律对象形」同一条规矩,也给日后加分页元信息留了位置。参数校验拒绝 `bool`(Python 里 `isinstance(True, int)` 为真,不拒的话 `limit=true` 会变成 `limit=1`)。
- **⑤ 规范合规**:无裸字面量(上下限仍取 `gameconfig`);注释讲「为什么」——尤其「room 不是路由字段」式的两处反直觉点:为什么响应要包一层、为什么参数校验不能截断。
- **⑥ 测试充分**:2 处变异确认。**如实记缺口**:(a) 没有测试钉住「全仓不再有明文 GET 端点」这条**全局**性质——现在靠三条各自的 `methods == {"POST"}`,日后新增一个明文读端点不会有任何东西变红;要防住得有一条遍历 `app.routes` 的守门测试,值得补但不在本批。(b) 授权范围没动,`user=` 仍可点名查任何人,这是**有意留的**(见下)。
- **⑦ 流程账本**:本篇即账本。开工前先去翻了 0057/0062 的原文,确认这是「回归既定设计」而不是新决定——这一点很重要:如果当成新决定,就会去讨论「要不要加密」,而实际要做的只是兑现。

### 有意没做,留档

- **授权范围**:登录用户仍可 `user=` 点名查任何人的手牌流水。收编只解决「传输裸奔 + 未登录可读」;要不要收紧成「只能查自己」是另一个决定,而且会连带决定前端历史页「全部」页签的去留,**用户尚未拍板**,已记进 [rest.md](../../rest.md) 的待定项。顺手收紧会悄悄改掉一个已上线的界面行为。
- **「禁止新增明文端点」的守门测试**:见上 ⑥(a)。
