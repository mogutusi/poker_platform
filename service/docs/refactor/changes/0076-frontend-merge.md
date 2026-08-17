# 0076 · 合并前端代码(YangBaiii/poker_platform → 本仓 frontend/)

日期:2026-08-17 · 性质:**代码合入 + 问题记档**(仅动 `frontend/` 与根 `.gitignore`,后端零改动)· 触发:用户「这是前端的代码,现在由我们来写前端了,你先把前端代码合并」。

上游:`https://github.com/YangBaiii/poker_platform`,分支 `develop`,HEAD `f7463fe`。

## 结论一句话

**只合了 `frontend/`,没有合上游的 `service/` 和 `lib/`**——上游是在整个重构之前分叉的,它的 `service/` 是 [0027](0027-prototype-teardown.md) 已经拆掉的旧原型,整仓合并会把 P0–P8 全部退回去。前端合入 79 个文件(+5602 行),UI 从 3 个组件的骨架变成三页完整界面,但**后端接线基本没做**,且 `api.ts` 打的是旧原型端点。

## 合入了什么

| 类别 | 内容 |
|---|---|
| 新页面 | `src/app/page.tsx` 登录页(243 行,重写)、`src/app/lobby/page.tsx` 大厅(407 行,新)、`src/app/game/page.tsx` 牌桌(556 行,新) |
| 新组件 | `PlayerInfoCard`、`TableSeat`,以及 shadcn/ui 的 `ui/{button,card,input,label}` |
| 新资源 | `public/cards/` 54 张牌面 PNG(4.3M)、`src/pics/` 4 张背景图(14M) |
| 改造 | `PokerCard` 从文字渲染改为加载 `public/cards/<rank><suit>.png`;`globals.css` 换成 Tailwind 4 + oklch 主题;`layout.tsx` 加 Orbitron 字体 |
| 依赖 | Next 14 → **15.5.4**,Tailwind 3 → **4.1.9**(postcss 插件改 `@tailwindcss/postcss`),引入 Radix + shadcn 全家桶 |
| 工具 | `components.json`(shadcn 配置)、`src/lib/utils.ts`(`cn` 助手)、`next-env.d.ts` |

## 没有合入 / 主动保住的

- **上游的 `service/` 与 `lib/` 一律不合**。上游 `service/app/` 里是 `pokertable/`、`user/`、`auth/`(含 `services.py.bak`)、`handrecord/`、`database/` + `main.py`/`app_route.py`/`init.py`——正是 0027 拆除的五包三入口。
- **上游的 `frontend/.next/` 不合**(400 个文件、78M 的 webpack pack 与图片缓存)。
- **本仓独有、上游没有的 4 个文件必须保留,已确认全部还在**:
  - `src/types/wire.gen.ts`(后端 codegen 产物,唯一事实源,禁止手写)
  - `src/utils/emoji.ts`(消费 codegen 的表情目录)
  - `BACKEND_GUIDE.md`(0070 起用户指示:协议/连接语义变更要同步这篇)
  - `crypto-test-vectors.json`(国密信道测试向量)

  上游 fork 早于这些文件产生,若按「上游覆盖本地」的方式合并会把它们删掉。

## 合并中发现的问题

### 阻断性(接线前必须解决)

**M1 · `api.ts` 打的全是旧原型端点,对不上现在的后端。**

```
/Texas/service/user/login   /api/auth/register
/api/games/{id}             /api/games/{id}/join|leave|action
```

现在的后端(见 [rest.md](../../rest.md) / [auth.md](../../auth.md)):

- 登录是 `POST /user/login`,请求体是 `{name, iv, blob}`,`blob` 是 `K_user` 加密的 `{password, client_nonce, ts}`,**不是明文 `{name, password}`**;返回 `{session_id, session_token, exp}`。
- 需身份的 REST 走加密信封 `POST {sid, frame}`;公开读是 `GET /lobby/rooms`、`GET /leaderboard`、`GET /hands`。
- **游戏动作全部走 WebSocket**(`/ws?sid=`,逐帧 SM4+HMAC-SM3 信封),没有 `/api/games/*/action` 这类 REST。

所以登录页现在连不上;大厅页和牌桌页的 API 调用本来就注释着(mock 数据)。

**M2 · 本机没有 node/npm,合并结果无法本地验证。** `npm install`、`next build`、`tsc --noEmit` 一个都跑不了。已做的是**静态检查**:全部本地 `import` 的解析目标都存在(0 断链);源码实际用到的 7 个第三方包在 `package.json` 里都有声明。类型错误、运行时错误、Tailwind 4 迁移是否真的可用,**都还没验证过**。这也是 [wire.md](../../wire.md) 里 REST DTO 的 TS 生成一直卡着的同一个原因。

### 需要处理但不阻断

**M3 · Tailwind 配置 v3/v4 并存。** `globals.css` 已是 v4 写法(`@import "tailwindcss"`),但 `tailwind.config.js` 还是 v3 风格(`content`/`theme` 导出),而 Tailwind 4 **不会自动读取 v3 的 config 文件**。结果是这份 config 里的 `content` 与主题扩展很可能被静默忽略。要么在 CSS 里加 `@config "../../tailwind.config.js"`,要么把主题迁进 CSS 的 `@theme` 块然后删掉这个文件。`components.json` 里也仍指着 `"config": "tailwind.config.js"`。

**M4 · `layout.tsx` 的 CSS 引入用了根绝对路径** `import '/src/styles/globals.css'`。合并前本仓写的是 `'./styles/globals.css'`,而 `layout.tsx` 在 `src/app/` 下,该路径指向不存在的 `src/app/styles/`——**本仓原来那句就是坏的**。上游这句在他们机器上能构建(有构建缓存为证),但根绝对路径依赖打包器的解析行为,不稳。惯用写法是 `'@/styles/globals.css'`(`tsconfig` 里 `@/*` → `./src/*` 已配好)。**没有擅自改**,留待有 node 能验证时一并处理。

**M5 · `package.json` 声明了约 50 个 Radix 包,源码实际只用到 2 个**(`react-label`、`react-slot`)。这是 shadcn/ui 模板的全家桶依赖,不是错误,但会让 `npm install` 和产物体积无谓变大。接线阶段可以按实际用量裁剪。

**M6 · 图片资源偏大。** `src/pics/poker-room.png` 单张 3.4M,`src/pics/` 合计 14M。作为背景图偏大,建议后续转 WebP/AVIF 或降分辨率。

**M7 · `src/types/poker.ts` 两边完全相同,`chips`/`phase` 与后端 enum 的漂移依然存在。** 这正是 [TODO.md](../TODO.md) 里「前端消费 wire.gen.ts」那条记的问题:`poker.ts` 是 UI mockup 的聚合类型,不是协议类型。新合入的 `game/page.tsx` 仍在用它(`import type { Card } from "@/types/poker"`)。接线时要把协议面换成 `wire.gen.ts`,mockup 类型只留给纯 UI 用途。

**M8 · 上游 `.gitignore` 没有 `.next/`**,这就是构建缓存被提交进库的原因。本次已在根 `.gitignore` 补上 `.next/`、`out/`、`*.tsbuildinfo`。

## 自 review

按 [review.md](../../review.md) 七维。本次不动后端代码,重点在 ②③⑦。

- **① 分层 / 不变量**:后端零改动,718 测试仍全绿(合并前后未触碰 `service/`)。前端不涉及后端不变量。
- **② 代码↔文档同步**:M1(端点对不上)、M7(poker.ts 漂移)都是「前端实现 ↔ 后端契约」的不一致,已如实记档并登进 TODO,没有假装合完就能用。
- **③ 文档↔文档一致**:本篇与 [BUGS.md](../BUGS.md)、[TODO.md](../TODO.md) 互链;`BACKEND_GUIDE.md` 本次未改(协议没变,按 0070 的规则无需同步)。
- **④ 数据模型正确性**:不适用。
- **⑤ 规范合规**:`wire.gen.ts` 保持为 codegen 产物、未手改;新增的 `.gitignore` 条目带了原因注释。
- **⑥ 测试充分**:**这是本次最大的缺口,不掩饰**——前端没有任何自动化验证,且本机无 node,连类型检查都跑不了(M2)。只做了静态的 import 解析与依赖声明核对。合入的 1200+ 行页面代码**一行都没被执行过**。
- **⑦ 流程账本**:本篇即账本。同时建立 [BUGS.md](../BUGS.md),把之前几轮(0072/0074)确认为真但未修的缺陷从 TODO 各处集中登记——用户点名要求「之前留的 bug 别忘了」。

## 待办 / 下一步

按依赖顺序:

1. **装 node 工具链**,跑通 `npm install` + `next build` + `tsc --noEmit`,把 M2 从「未验证」变成「已验证」;顺带解掉 M3、M4。
2. **重写 `api.ts` 对齐真实后端**(M1):登录改 `POST /user/login` 的加密信封流程,公开读接 `/lobby/rooms`、`/leaderboard`、`/hands`。
3. **接 WebSocket**:按 [wire-protocol-guide.md](../../wire-protocol-guide.md) 实现 SM4+HMAC-SM3 逐帧信封 + `ClientMessage`/`ServerMessage` 收发,替换 `game/page.tsx` 的 mock 牌局。
4. **协议类型换 `wire.gen.ts`**(M7),`poker.ts` 退回纯 UI 用途。
5. 裁剪依赖(M5)、压缩图片(M6)。
