# 0067 · wire 协议指南 truth-up(REST 面 + 动态建房 + 登录/信封契约)+ TODO 收账

日期:2026-07-13 · 范围:`docs/wire-protocol-guide.md`、`docs/refactor/TODO.md`。纯文档单元(指南是前端对接的契约文档,即 W 阶段 `[~]`「协议指南」项的本体)。

## 背景 / 为什么

指南自 0017 起笔、0047 修过 §9,但 0048–0066 落地的一整片东西没进来,已有**失真**(不是缺漏是错误):§3 说 `join_room` 对不存在的房回 `NO_SUCH_ROOM`——0049 起是**动态建房**;§8 把「大厅房间列表(REST)」列在「还没有」——0048 已落地。缺漏:REST 三读(lobby/leaderboard/hands)、profile 三件、REST 信封契约(0062:`{sid, frame}`/分域密钥/滑动窗/seq 回显/重试规则)、登录 blob 形状(0063:`ts`+`client_nonce`)与 `rotate` 提示(0066)、聊天表情 `[code]` 渲染(0035)。前端拿着这份指南写 client 会直接写错。

另:P1 两个 `[~]`(`core/reduce.py` / `tests/core/`)所列子砖已全部落地(0002–0049),该收账打勾;W 指南项本次补齐后也可打勾(后续仍随模块增量,归「持续项」)。

## 打算改什么

- 指南 §3:`join_room` 行改动态建房语义(房不存在则创建、创建者无特权、参数用服务端默认、`NO_SUCH_ROOM` 仅防御臂)。
- 指南 §4 表后补一条:聊天正文的 `[code]` 表情 token 渲染(`EMOJI_CATALOG` 在 `wire.gen.ts`,`utils/emoji.ts` 已有 tokenizer;房聊/私聊同规则;未知 code 原样)。
- 指南新增 §10「REST 面」:公开三读(lobby/rooms 轮询、leaderboard、hands 游标分页)+ 登录(blob=`{password, client_nonce, ts}`、响应含 `rotate` 换钥提示)+ 需身份端点的会话信封契约(`{sid, frame}`、REST 域密钥与 ws 分域、seq 严格递增+响应回显、重试=新 seq 重封、401/500 两段式)+ profile 三件;并明示 REST DTO **暂无 TS 生成**(无 node),字段以 `app/rest/*.py` 为准。
- 指南 §8:已交付补 REST 面;「还没有」改为 REST 的 TS 生成 + 前端 WS client 本身。§9 登录一句改指 §10。
- TODO:P1 两 `[~]` → `[x]`(收账注明);W 指南项 → `[x]`(本次补齐至 0066 全量,后续增量归持续项)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地,全部为文档:

- 指南 §3 `join_room` 行:动态建房(0049)替换失真的「不存在 → `NO_SUCH_ROOM`」;标明 `ALREADY_IN_ROOM` 是唯一常规失败、`NO_SUCH_ROOM` 为防御臂。
- 指南 §4 表后注:聊天 `[code]` 表情 token 渲染(房聊/私聊同规则、`EMOJI_CATALOG` 在 `wire.gen.ts`、`utils/emoji.ts` 已有 tokenizer、未知 code 原样)。
- 指南新增 §10「REST 面」:公开三读表(`/lobby/rooms` 轮询、`/leaderboard`、`/hands` 游标分页含 `participants[].net`)+ 登录块(blob=`{password, client_nonce, ts}`、响应 `{session_id, session_token, exp, rotate}`、`rotate` 换钥提示、失败统一 401、重试须新 nonce+新 ts)+ 会话信封契约(`{sid, frame}`、内层 `seq(8B,BE)‖JSON`、REST 域密钥 0x03/0x04 与 ws 0x01/0x02 分域、seq 严格递增+响应回显、重试=新 seq 重封、401/500 两段式)+ profile 三件表(状态码 400/403/409/500 对齐 profile.py)。
- 指南 §8:已交付补表情目录 + REST 面;「还没有」改为 REST 的 TS 生成(无 node)+ 前端 WS client 本身。§9 登录一句改指 §10(§9 编号未动,外部引用「guide §9」不破)。
- TODO:P1 `core/reduce.py` / `tests/core/` 两 `[~]` → `[x]` 收账(子砖 0002–0049 全落);W 指南项 → `[x]`(0067 truth-up,后续增量归持续项)。

## 自 review

对照 [review.md](../review.md) 逐维;**对抗式多智能体审计两次均中途撞会话额度身亡**(两 lens finder 各跑 ~330s 被杀),改由主线**逐断言对码核实**(纯文档变更,风险面即「指南断言 vs 代码」,可直接验),record 如下:

- **① 分层 / 不变量**:无代码变更,不适用。
- **② 代码↔文档同步(本次主体,逐条验过)**:§3 动态建房 ↔ `reduce._join_room`(建房臂/`ALREADY_IN_ROOM` 先于建房/`NO_SUCH_ROOM` 仅 `create=None` 防御);§4 表情 ↔ `wire/emoji.py` + `wire.gen.ts:339 EMOJI_CATALOG` + `utils/emoji.ts`(未知原样有注);§10 三读字段 ↔ `RoomMeta`/`LeaderboardEntry`/`HandRecordView`+`HandParticipantView.net` 逐字段比对;登录形状 ↔ `login.py`(`{name,iv,blob}`/响应含 `rotate`/`exp`);信封 ↔ `secure.py`+`channel.py`(`_SEQ_BYTES=8` 大端、`_KDF_INFO_REST_* = 0x03/0x04`、回显、401/500 分层);profile 状态码 ↔ `profile.py`(400/403/409/500 各臂)。
- **③ 文档↔文档一致**:指南全篇 grep 无残余失真(`NO_SUCH_ROOM` 仅存于已修正行);§8「还没有」与 wire.md「REST 走 openapi-typescript 待 P7」一致;§9 编号未动(TODO 里「guide §9」引用不破);0067 打算↔实际一致。
- **④ 数据模型**:不适用(无模型变更)。
- **⑤ 规范合规**:指南延续原文体例(表格 + 铁律式短句);不复制字段清单进 md 的红线守住——§10 表格给的是「怎么调」的契约(端点/语义/错误码),并明示形状以 .py 为准(REST 无 codegen 是被迫例外,已在文首注明)。
- **⑥ 测试充分**:纯文档,无测试面;`pytest` 688 全绿未动。
- **⑦ 流程账本**:变更记录先行;TODO 三处打勾各附收账理由;提交引用 0067、全英文。

**发现与处置**:主线核对 0 失真(所有断言与代码一致);两次审计 workflow 因外部额度中断属基建故障非发现缺失——已用「逐断言对码」的等价手工核实覆盖同一风险面。0 未处置发现。
