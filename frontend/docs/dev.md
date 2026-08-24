# 前端开发约定

## 环境

Node 装在用户目录(本机没有 sudo),已写进 `~/.zshrc`:

```sh
export PATH="$HOME/.local/node/bin:$PATH"
```

版本:Node 24.19.0 LTS + npm 11.17.0。首次装依赖后需要批准三个包的安装脚本(`@tailwindcss/oxide` 是 Tailwind 4 的原生二进制,`sharp` 是 Next 的图片优化,`unrs-resolver` 是 eslint 解析器):

```sh
npm approve-scripts @tailwindcss/oxide && npm approve-scripts sharp && npm approve-scripts unrs-resolver
```

## 常用命令

```sh
cd frontend
npm run dev          # 开发服务器
npm run build        # 生产构建(会跑 lint + 类型检查)
npm run type-check   # 只跑 tsc --noEmit
npm run lint         # eslint
npm test             # vitest:加密向量 + 状态归并 + seq 纪律
npm run smoke        # 协议层端到端冒烟:一手牌全程(需后端在跑,见下)
npm run smoke:raise  # 加注 / min-raise / 三人边池(0085)
npm run smoke:stale  # 「上次会话残留」自愈(0078·A)
npm run test:e2e     # 真实浏览器走用户旅程(需后端在跑)
```

`npm run build` 是**提交前必须跑通的门槛**。它会一并做类型检查和预渲染,能抓到 `tsc` 单独跑不出来的问题(例如 `useSearchParams` 没包 Suspense,0077 就是这么抓到的)。

## 技术栈

Next 15(App Router)· React 18 · TypeScript · Tailwind 4 · shadcn/ui(Radix)· vitest。

Tailwind 4 的配置在 CSS 里(`src/styles/globals.css` 的 `@import "tailwindcss"` + `@theme`),**不是** `tailwind.config.js`。v4 不会自动读 v3 风格的 config 文件。

## 目录

```
src/
  app/            页面路由:/ 登录 · /lobby 大厅 · /game 牌桌
  components/     组件;ui/ 下是 shadcn 生成的基础件
  crypto/         国密原语(见 docs/crypto.md)
  transport/      登录、ws 信道、REST 信封(见 docs/transport.md)
  store/          客户端状态
  types/          wire.gen.ts(后端产物,只读)+ poker.ts(UI 类型)
  utils/          纯工具
  styles/         全局样式
docs/             本目录:前端设计文档
public/cards/     54 张牌面图
```

## 纪律

- **`src/types/wire.gen.ts` 禁止手改**。它是后端 `service/scripts/gen_wire_ts.py` 的产物。要改协议,改后端 `.py` 再重新生成。后端有 `tests/wire/test_codegen_uptodate.py` 守着漂移。
- **`crypto-test-vectors.json` 禁止手改**。它是后端生成的验收标准。
- **改到协议、连接语义、关闭码、加密细节** → 同一次改动里同步 [frontend/BACKEND_GUIDE.md](../BACKEND_GUIDE.md)(用户指示,0070 起)。
- **不提交构建产物**:`.next/`、`out/`、`node_modules/` 都已在根 `.gitignore` 里。上游 fork 曾把 `.next/` 提交进库(400 文件 / 78M),别重蹈覆辙。
- 秘密不进日志:`session_token`、`K_user`、密码不许 `console.log`。

## 提交前

1. `npm run build` 通过。
2. `npm test` 通过(加密向量必须全绿——差一个字节就连不上后端,且报错毫无提示)。
3. 按 [service/docs/review.md](../../service/docs/review.md) 做一次自 review,结论写进 `service/docs/refactor/changes/NNNN`。
4. 提交信息全英文,引用变更号。

## 和后端一起跑

后端(另开一个终端):

```sh
cd service && .venv/bin/python -m uvicorn app.shell.lifespan:app --reload
```

前端默认连 `http://localhost:8000`,可用 `NEXT_PUBLIC_API_URL` 覆盖。dev 阶段后端还留着明文 `?nick=` 的 ws 端点,但**前端一律走加密端点**,不用明文那条(它会随前端切完加密退役)。

### 端到端冒烟

三个冒烟脚本共用 `scripts/smoke-client.mjs`(登录握手 + 加密 ws + 收发帧 + 残留房处理)。**别再复制一份**:它直接压在协议面上,协议一改所有副本都得跟着改,漏一份就是一个静默失效的冒烟。

`npm run smoke` 用本前端自己的加密代码,对真后端跑一遍:登录 → ws 握手 → 进房 → 入座 → 买入 → 准备 → 开局 → 打完一手 → 聊天 → 离桌,并检查底牌隐私、seq 单调、离桌后筹码守恒。

**这是检验传输层的唯一可靠办法**——类型检查和构建都发现不了「密钥派生错一个字节」这种问题,而它的表现只是服务器默默关连接。

前置:后端在跑,且本地库 schema 是最新的。库过时(缺鉴权列)会让后端起不来,重建方式:

```sh
cd service && rm -f poker.db && .venv/bin/alembic upgrade head
```

脚本每轮用独立房名,结束时 `leave_room` 退分,所以可以反复跑。它用的是 dev 种子用户和 dev 共享密钥,仅限本地。

### 浏览器测试

`npm run test:e2e` 用 Playwright 开真 Chromium 走一遍:登录页校验 → 密码错的提示 → 登录进大厅 → 进房看到观战提示 → 未登录直接开牌桌页被送回登录页。前端由 Playwright 自动起,后端要自己起。

它补的是另外两层都盖不到的面:按钮点了有没有反应、页面会不会白屏、有没有未捕获的运行时错误。0079 就是靠它抓到 CORS 没配、空大厅没有进房入口、以及 ws 连接的一处竞态;0087 靠它抓到「每次重连都把自己从座位上退下来」「整轮 preflop 的跟注都发成 0」「被顶替后两边无限互顶」。

**断线怎么造:关那条 socket,别用 `context.setOffline(true)`。** 实测(2026-08-24,chromium)`setOffline` 只挡新请求,**已经建立的 WebSocket 照常活着**,断线横幅根本不出现,于是「重连之后」的断言全都在没断过线的页面上跑,一路假绿。`e2e/reconnect.spec.ts` 的做法是用 `addInitScript` 包一层 `window.WebSocket`(顺带记下每条连接的**关闭码**——`page.on('websocket')` 给不了这个),再从页面里把那条 socket `close()` 掉。

**每个用例用不同的 dev 账号**——同一账号在两个用例间会互相顶替连接,也可能把上一轮的房间残留带过来。

**用例共用的脚手架在 [`e2e/helpers.ts`](../e2e/helpers.ts)**:登录、进房入座、以及**推进手牌**。推进那部分尤其别自己再写一份——按钮不按规则灰(合法与否由服务器裁定,这是前端不变量 1),所以「先点 Check 再点 Call」这种写法在 heads-up preflop 必被 `ILLEGAL_ACTION` 拒;点完不等服务器表态就往下走的话,循环会一路空转到 `ACTION_TIMEOUT`(15 秒),最后由服务器替人默认弃牌收场,而用例**照样绿**。`showdown.spec.ts` 就这么假绿了两批,直到 0087 才发现它自称验过的三条街一次都没走到。`helpers.ts` 的 `actAndWait` 会等到服务器表态(桌面动了 = 接受 / 弹出错误 = 拒绝),两个动作都被拒就直接抛。

**结束时还停在手牌里的用例会卡住下一个用同名账号的用例**(0089 实测教训)。`table`/`raise`/`reconnect` 打完就关浏览器,人还坐在牌桌上、手牌没打完;下一条用例拿同一个账号登录,走的是「先退再进」,而**局中的 `leave_room` 要等这手打完才驱逐**([rules.md](../../service/docs/rules.md) ④),于是它连房都进不去——单跑绿、全套必红,而且报的是「找不到某个按钮」,离真因很远。需要真的进房的新用例,给它一个没人用的 dev 账号(`gina` 就是这么来的)。

**冒烟脚本另有一套专属账号 `smoke1`/`smoke2`/`smoke3`,不要拿它们写浏览器用例**(0086 实测教训)。共用账号还有第二种更隐蔽的串扰:浏览器用例结束时玩家还坐在桌上,占座窗口(`LIVENESS_TIMEOUT`,默认 90 秒)满了才由 `Cleanup` 把桌上筹码退回全局积分——这笔退款可能正好落在冒烟跑到一半的时候,凭空改变「两人合计」,把守恒断言打红。查这种红最费时间,因为**产品完全没问题**,是另一个测试在动同一批账号。

### 一个会浪费很多时间的坑

**`npm run dev` 跑着的时候别跑 `npm run build`。** 两者共用 `.next` 目录,构建会把 dev server 正在用的 chunk 冲掉。症状很有迷惑性:页面照样返回 200,但浏览器里静态资源 404、React 没水合、所有 `useEffect` 都不执行——看起来像是组件逻辑坏了,实际只是产物被覆盖。

清理:停掉 dev server → `rm -rf .next` → 重新起。
