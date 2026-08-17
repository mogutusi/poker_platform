# 0077 · 前端开工:工具链 + 设计文档 + 国密信道 + 登录接线

日期:2026-08-17 · 性质:**前端建设**(后端零改动)· 触发:用户「M1、M2 没关系,你改成我们后端设计的,前端也归我们了;M2 你自己安不就好了。尽可能使用已经设计好的场景,然后开工前端,开工之前的文档、工程设计你自己决定,可以参考后端的」。

## 结论一句话

装上 node 工具链,把 [0076](0076-frontend-merge.md) 记的 M2(无法验证)彻底解掉——顺手抓出两个静态检查看不见的真错误;给前端立了 5 篇设计文档;用 TypeScript 实现国密信道并**用后端生成的向量逐条验过(29/29)**;登录页已接真握手。大厅页接了真实的房间列表与排行榜。牌桌页尚未接线。

## 一、工具链(M2 解决)

本机无 sudo,所以把 Node 官方 tarball 装到用户目录,并写进 `~/.zshrc`:

```
~/.local/node/  →  Node 24.19.0 LTS + npm 11.17.0
```

npm 11 默认拦截依赖的安装脚本,需要显式批准三个:`@tailwindcss/oxide`(Tailwind 4 的原生二进制)、`sharp`(Next 图片优化)、`unrs-resolver`(eslint 解析器)。不批准的话 Tailwind 直接不工作。

### 装上之后立刻抓到两个真错误

这两个都是 0076 里「静态检查通过」但实际跑不起来的东西,正好印证了当时把 M2 记成阻断项是对的:

1. **`tsconfig.json` 的 `target` 是 `es5`**(Next 13 时代的默认值),导致 `src/utils/emoji.ts` 里 `matchAll` 的迭代直接编译报错。升到 `ES2020`。
2. **`/game` 页面构建失败**:Next 15 要求 `useSearchParams()` 必须落在 Suspense 边界内,否则整页预渲染报错。把原 `GamePage` 拆成内层 `GameView`(用 searchParams)+ 外层给 `<Suspense>` 边界。

现在 `npm run build`、`npm run type-check`、`npm test` 三项全绿。

## 二、设计文档([frontend/docs/](../../../../frontend/docs/))

参照后端 `service/docs/` 的做法,一篇一个主题,开头一句话定位:

| 文档 | 讲什么 |
|---|---|
| `architecture.md` | 分层(crypto → transport → store → app)、数据流、6 条前端不变量 |
| `crypto.md` | 四个国密原语的精确定义、验收方式、JS 特有的坑 |
| `transport.md` | 登录握手、信封格式、**seq 纪律**、ws 重连、秘密存放分级 |
| `state.md` | 快照为真相 + 事件增量、三个场景到命令的映射、行动按钮怎么算 |
| `dev.md` | 环境、命令、纪律、和后端一起跑 |

**最重要的一条设计决定:服务器是唯一真相。** 前端不复算规则、不预测、不本地推进牌局。合并进来的 UI 里那套本地 mock 牌局(`createDeck` 发牌 + 本地推进街道)是没有后端时的占位,接线时整套拆掉,不保留任何「双份真相」。

## 三、国密信道(TypeScript)

后端没有 TLS,自己用国密做了一层加密信道,前端必须实现同一套原语,否则连都连不上。

`src/crypto/`:`sm3.ts`(SM3 杂凑)、`sm4.ts`(SM4 分组 + CBC + PKCS#7)、`index.ts`(HMAC-SM3、KDF、信封的 seal/open、字节工具)。

### 为什么自己实现而不用现成库

必须和后端那份 `ttxsgm` **逐字节一致**——版本或填充策略的差异都会导致 MAC 失败,而 MAC 失败的表现是服务器直接关连接、没有任何有用报错。自己实现 + 用后端生成的向量守门,比赌一个第三方库的行为更可控。代码量也不大(SM3 约 90 行,SM4 约 150 行)。

S 盒没有手敲,是从后端 `lib/ttxsgm/ttxsgm/sm4.py` 里抽出来并校验为 0–255 的置换后生成的。

### 验收:29/29 向量通过

[crypto-test-vectors.json](../../../../frontend/crypto-test-vectors.json) 是后端 `scripts/gen_crypto_vectors.py` 的产物,`src/crypto/crypto.test.ts` 逐条比对:

- SM3 五条(含空串、UTF-8 中文、55/64 字节的分组边界);
- SM4-CBC 加解密双向(含「明文恰为 16 整数倍时仍要补满一整块」这个坑);
- HMAC-SM3、KDF 四个域(ws enc/mac + REST enc/mac);
- **完整 ws 帧**:封出的 `iv‖ct‖mac` 与后端逐字节相同;
- **完整 REST 信封**、**登录 blob 双向**。

另加三条自己写的对抗测试:篡改帧内任意一字节 MAC 必败;用 REST 密钥拆 ws 帧必败(证明密钥分域真的挡住跨信道重放);拆帧能还原 seq 与明文。

## 四、传输层

`src/transport/`:

- **`session.ts`** —— 秘密分级存放:`K_user` 进 `localStorage`(每周轮换、要跨会话留存),`session_token` **只在内存**(能派生所有帧密钥,泄露等于会话被接管;刷新页面即重登)。seq 计数器也在这里。
- **`login.ts`** —— 登录握手:`K_user` 加密 `{password, client_nonce, ts}` → `POST /user/login` → 解出 `{session_id, session_token, exp, rotate}`。每次登录都用新 nonce 和当前 ts(服务器有 freshness + nonce 去重两道重放守卫)。`rotate=true` 时提示用户在用旧钥、尽快更换。
- **`ws.ts`** —— 二进制帧收发、按「验 MAC → 解密 → 验 seq」的入站铁序处理、退避重连;关闭码 `4401` 视为鉴权失效,不自动重连而是要求重登。
- **`rest.ts`** —— 公开读(`/lobby/rooms`、`/leaderboard`)明文 GET;需身份的走 `{sid, frame}` 信封。

### 最容易踩的坑,已在代码和文档里都钉住

**seq 按会话计,不按连接计。** ws 断线重连后 seq 必须接着往上加;从 0 重来会被服务器判 `stale_seq` 拒掉,然后连接被关,陷入重连死循环。只有重新登录换了会话才归零。

## 五、场景接线

沿用已经设计好的三个场景,不重画界面。

**登录页 `/`(已接通真握手)**

- 删掉 `admin/123456` 的 mock 登录旁路,和往 `localStorage` 写假 token 的写法。
- **新增 `K_user` 输入框**:原设计只有账号密码,但没有 `K_user` 就无法构造登录 blob,登不上。本地已缓存就不再要求填,只留一个「换一把 K_user 密钥」的入口。视觉沿用密码框那一套。
- 服务器对「账号不存在 / 密码错 / blob 坏」一律回 401 且不区分,所以前端也只给笼统提示,不猜原因误导用户。

**大厅页 `/lobby`(接了真数据)**

- 排行榜换成 `GET /leaderboard`,房间列表换成 `GET /lobby/rooms`,登出改为断开 ws + 清会话。
- **一处诚实的取舍**:原 UI 的大厅直接画了一张 9 座的桌子并显示每个座位上是谁。但 `GET /lobby/rooms` 只给汇总(`seated`/`max_seats`),给不出「谁坐哪」——逐座位详情要 `join_room` 之后由 `StateSnapshot` 带来。所以座位改成**匿名占位**(按 `seated` 数渲染「已入座」),**不编造玩家名**;点座位即进入该房间。真实的座位视图留给牌桌页。

**牌桌页 `/game`(尚未接线)**

除了修掉 Suspense 那个构建错误外没动。它现在仍是本地 mock 牌局。

## 六、删除

`src/lib/api.ts` 删除。它打的是旧原型端点(`/Texas/service/user/login`、`/api/games/*`),即 0076 记的 M1;现在真实的通信全在 `src/transport/` 里,留着它只会误导人再去调。

## 自 review

按 [review.md](../../review.md) 七维。

- **① 分层 / 不变量**:后端零改动。前端的分层不变量写进了 `architecture.md`,本次代码守住:`crypto/` 无 IO;`transport/` 不 import 任何组件;组件里没有 `new WebSocket`,也没有手拼加密帧。
- **② 代码↔文档同步**:5 篇设计文档与本次实现同批写、同批改。文档里的信封格式、KDF 定义、入站铁序都是照着 `app/auth/channel.py` 和 `auth.md` 核过的,不是凭印象写的。
- **③ 文档↔文档一致**:前端文档链回后端对应文档,并在 `architecture.md` 末尾给了对应表。
- **④ 数据模型正确性**:`RoomMeta`/`LeaderboardEntry` 的字段照 `app/rest/*.py` 抄;协议类型一律来自 `wire.gen.ts`,没有手写第二份。
- **⑤ 规范合规**:`wire.gen.ts` 与 `crypto-test-vectors.json` 都未手改。秘密不进日志。注释解释的是「为什么」(seq 为何跨重连累加、MAC 为何必须先验),不是复述代码。
- **⑥ 测试充分**:加密层 29 条向量 + 3 条对抗测试全绿,这是本次质量的主要保证。**但要说清缺口**:传输层(登录流程、ws 重连、seq 累加)**没有自动化测试**,只有类型检查和构建通过;真实的端到端连通(前端 ↔ 后端跑通一手牌)**尚未做过**。这是下一步最该补的。
- **⑦ 流程账本**:本篇即账本。0076 的 M1(旧端点)与 M2(无法验证)已解决;M3(Tailwind v3/v4 并存)、M4(CSS 根绝对路径)、M5(依赖冗余)、M6(图片过大)、M7(poker.ts 漂移)仍在,已留在 TODO。

## 待办 / 下一步

1. **牌桌页接线**:`join_room` → `StateSnapshot` → 事件增量;拆掉本地 mock 发牌与街道推进;动作按钮发 `player_action`(注意 `bet_amount` 是**本街目标总额**,不是增量)。
2. **端到端冒烟**:起后端 + 前端,真跑通「登录 → 进房 → 入座 → 买入 → 准备 → 开局 → 一手牌」。这是检验传输层的唯一可靠办法。
3. **传输层补测**:seq 跨重连累加、拆帧失败不误伤、4401 不自动重连——这几条现在只有代码和文档在保证。
4. 0076 遗留的 M3–M7。
