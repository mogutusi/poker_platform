# 0101 · Tailwind 配置只剩一个能用的 token,而它恰好是坏的(0076·M3 + M4)

日期:2026-08-27 · 性质:**缺陷修复(前端配置)**· 触发:[TODO.md](../TODO.md) 0076·M3(Tailwind v3/v4 并存)与 M4(`layout.tsx` 的根绝对 import)。

## 登记说是「配置并存不整洁」,实际有个活的缺陷

`src/styles/globals.css` 第一行是 `@import "tailwindcss"` —— 这是 **Tailwind v4**(装的是 4.1.9)。而 `tailwind.config.js` 是 v3 风格的 CommonJS,**v4 根本不读它**。所以那份配置里的东西全是空转:`poker.*` 五个颜色、`card.*` 两个、`font-sans`、两个动画、以及 `@tailwindcss/forms` / `@tailwindcss/typography` 两个插件。

数了一遍引用,整份配置里**只有一个 token 被用到**:

```
poker-green   1 处 —— src/app/layout.tsx:25  bg-gradient-to-br from-poker-green to-green-900
poker-gold / poker-red / poker-black / poker-white / card-bg / card-border
animate-card-flip / animate-chip-bounce                             全部 0 处
```

而这唯一被用到的那个,**正是坏的那个**。实证:`npm run build` 产出的 `.next/static/css/app/layout.css` 里 grep `0f5132`(`poker-green` 的色值)——**0 命中**。也就是说全站根容器的渐变 `from-poker-green to-green-900` 一直只有后半截生效,起始色是空的。这不是整洁性问题,是一处一直在线上的视觉缺陷。

(那两个动画和 `poker-gold` 的最后消费者是 `Chip.tsx`,已于 [0099](0099-retire-the-mockup-types.md) 随孤儿组件删除——所以这份配置是**先失效、后被掏空**的。)

## 打算怎么改

v4 的方向已经定了(`globals.css` 用的是 v4 的 `@import` 与 `@custom-variant`),所以**不加 `@config` 把 v3 文件救回来**,而是把**还在用的那一个** token 迁进 CSS 的 `@theme`,然后删掉配置文件:

1. `globals.css` 加 `@theme { --color-poker-green: #0f5132; }` —— v4 的 CSS-first 写法,`from-poker-green` 随即可用。
2. 删 `tailwind.config.js`(其余 token 零引用,删掉即可;真要用从 git history 取回)。
3. `components.json` 的 `tailwind.config` 指向该文件,改成空串(shadcn 在 v4 下的约定)。
4. **M4**:`layout.tsx` 的 `import '/src/styles/globals.css'` 改成 `'@/styles/globals.css'`(根绝对路径不稳,合并前那句 `'./styles/globals.css'` 本来就是坏的)。

**不动依赖**:`@tailwindcss/forms` / `@tailwindcss/typography` 现在确实一行没生效,但裁剪依赖会动 lockfile,而且 0076·M5 已经单独登记了「约 50 个 Radix 包实际只用 2 个」的裁剪项——归那一批一起做更合适。

## 实际改了什么

按计划落地,四处:

- **`src/styles/globals.css`**:紧跟 `@custom-variant` 之后加一个 `@theme`,只放 `--color-poker-green`,并注明其余 token 为何随文件一起删。
- **删 `tailwind.config.js`**。
- **`components.json`** 的 `tailwind.config` → `""`。
- **`src/app/layout.tsx`**:`'/src/styles/globals.css'` → `'@/styles/globals.css'`(M4)。

**顺手踩到并修掉的一个坑**:注释里原本写了 `poker-*/card-*`,那个 `*/` 会**提前闭合 CSS 注释**,后半段注释就变成了实际样式规则。写完当场看出来改了——在 CSS 注释里写通配符路径是个现成的陷阱。

## 验证

关键判据不是「测试还绿」(它们本来就绿,这个缺陷正是它们看不见的那类),而是**色值有没有进产物**:

| | `.next` 产物里 grep `0f5132` |
|---|---|
| 改之前 | **0 命中** ← `from-poker-green` 是死的,渐变只有后半截 |
| 改之后 | **1 命中** |

这一进一出等价于反向变异验证:把 `@theme` 去掉就回到 0 命中。

| 层 | 结果 |
|---|---|
| `tsc --noEmit` | 通过 |
| `npm run build`(先 `rm -rf .next` 全新构建) | 通过 |
| 前端 vitest | 93 passed |
| 浏览器 `npm run test:e2e` | **16 passed** |
| 冒烟 | 通过 |
| 后端 | 未改动 |

## 自 review

按 [review.md](../../review.md) 七维。本批是**前端构建配置**,最高风险面是「删掉的东西真没人用」与「样式是否被悄悄改坏」。

- **① 分层 / 不变量**:后端零改动;前端不变量不涉及(不碰协议、不碰 store)。
- **② 代码↔文档同步**:`globals.css` 的 `@theme` 注释写清了「v4 是 CSS-first、v3 配置不再被读取」,免得下一个人又建一个 `tailwind.config.js` 却发现不生效。
- **③ 文档↔文档一致**:[TODO.md](../TODO.md) 的 M3/M4 都勾掉,并把「登记以为是整洁性问题、实际是活缺陷」写进条目。
- **④ 数据模型正确性**:不适用。
- **⑤ 规范合规**:删死配置兑现「不留死代码」;注释讲「为什么」。
- **⑥ 测试充分**:**如实记两个缺口**。(a) 这类缺陷**没有任何自动化能发现**——16 个浏览器用例、93 个单测、构建全绿,而那个渐变一直是坏的:测试断言的是行为与文本,没有一处断言样式。要防住得有视觉回归(截图比对),那是另一套设施。(b) 我**没有肉眼确认**改后的渐变观感,只确认了色值进入产物;如果 `#0f5132` 这个值本身当初就选错了,本批不会发现——但它至少现在真的生效了,而此前是空的。
- **⑦ 流程账本**:变更记录先行;开工前先数引用 + 查产物,**结论比登记更重**(登记说「不整洁」,实测是线上视觉缺陷),这一点写进了记录正文。

### 有意不做

- **`@tailwindcss/forms` / `@tailwindcss/typography` 两个依赖**:配置失效后它们一行没生效,现在删了配置更是彻底没人加载。但裁依赖要动 lockfile,而 [TODO.md](../TODO.md) 0076·M5 已单独登记「约 50 个 Radix 包实际只用 2 个」的裁剪项——归那一批一起做,一次 `npm install` 说清楚。
- **视觉回归设施**:见 ⑥(a),值得单独议。
