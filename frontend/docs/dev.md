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
npm run smoke        # 端到端冒烟(需后端在跑,见下)
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

`npm run smoke` 用本前端自己的加密代码,对真后端跑一遍:登录 → ws 握手 → 进房 → 入座 → 买入 → 准备 → 开局 → 打完一手 → 聊天 → 离桌,并检查底牌隐私、seq 单调、离桌后筹码守恒。

**这是检验传输层的唯一可靠办法**——类型检查和构建都发现不了「密钥派生错一个字节」这种问题,而它的表现只是服务器默默关连接。

前置:后端在跑,且本地库 schema 是最新的。库过时(缺鉴权列)会让后端起不来,重建方式:

```sh
cd service && rm -f poker.db && .venv/bin/alembic upgrade head
```

脚本每轮用独立房名,结束时 `leave_room` 退分,所以可以反复跑。它用的是 dev 种子用户和 dev 共享密钥,仅限本地。
