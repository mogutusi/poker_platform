# 0001 · 设计文档一致性修复 + 建立重构总纲

日期:2026-06-17 · 范围:`service/docs/`(只动文档,未动代码)

## 背景

开始写实现前,对全部 21 篇设计文档做了一轮交叉评审,发现几处**真实建模缺口**和一批**模型 1 → 模型 2 迁移的残留**(命令带 room / origin 是 (room,nick) 元组)。这些会在写实现时反复打架,先一次性钉死。

## 改了什么

### A. 真实建模缺口

1. **`UserState` 加不可变 `uid`(= DB `User.id`)。** 落库/手牌记录一律按 `uid`,不按可变的 `nickname`(nickname 仍是 `world.users` 的键,但只能在大厅改)。
   - `user.md`:`UserState` 加 `uid` 字段 + 注意点;`JoinRoom(room, uid, loaded)` 载入带 uid;BuyIn 示例改 model-2 + `PointsWrite(uid=...)`。
   - `core.md`:`JoinRoom` 命令签名带 uid;手牌记录 participant 的 uid 由 `work.users[nick].uid` 取。
   - `db.md`:`PointsWrite.uid`、`StateKey=("user", uid)`、participant 含 uid。
   - `lobby.md`:`JoinRoom` 签名 + 安装 `UserState(uid, ...)`。

2. **`checkout(cmd)` / `commit(work)` API 钉死。** 原伪码 `checkout(cmd.room)` 太简化——模型 2 命令多不带 room。
   - `storage.md`:新增「按命令类型解析目标房」表(JoinRoom 自带 / 其余取 `users[origin].room` / Timeout·Cleanup·Connect·Disconnect 视情况)+「commit 处理房间增/删/替换」小节。明确 **GameLoop 读 `world.users` 解析房间是允许的**(它是唯一写者)。
   - `core.md`:房间生命周期改为 **v1 静态预置、动态建房 future**,创建走 `JoinRoom` 非 `Connect`。
   - `architecture.md`:GameLoop 伪码 `checkout(cmd)`。

3. **房间销毁时的 Broadcast 容错。** 最后一人离开会 `del world.rooms[room]`,而 dispatch 仍按 `world.rooms[r]` 找成员 → KeyError;离开者已被移出成员名单收不到回执。
   - `connection.md`:dispatch 的 Broadcast 加 `rooms.get` 容错;新增「销毁房 / 离开者确认」说明(离开者用 `Personal(UserLeft)`,留下的人用 `Broadcast`)。
   - `core.md`/`lobby.md`:销毁不再 Broadcast;`LeaveRoom` 产 `Personal(离开者, UserLeft)` + `Broadcast`。

### B. 模型 1 残留清理

- **`timer.md`**:`_liveness` 从 `(room,nick)` 键改为 **nick 单键**(Receiver 只知 nick、不准读 world);`heartbeat(nick)`/`drop_liveness(nick)`;`Cleanup(nick)`/`Timeout(nick,epoch)` 不带 room;补「为何按 nick」说明。`_action` 仍按 room 键(reduce 经 TurnChanged 给 room)。
- **`error.md`**:`origin` 从 `(room,nick)` 元组退成 **nick**;`send_error` 用 `conns.get(cmd.origin)`;reduce/validate 示例改 model-2(`cmd.origin`、`work.users[cmd.origin].room`)。
- **`architecture.md`**:`Disconnect(nick)`、`Personal(nick, msg)`(去掉 room)。
- **术语统一**:全库 `origin_nick` → `origin`(architecture/connection/messaging/wire)。

## 对设计的影响

- 不变量未变;以上都是**让文档自洽到模型 2 + 补齐 uid/checkout/销毁三个缺口**,不引入新机制。
- 唯一新增字段:`UserState.uid`、`PointsWrite.uid`、participant `uid`——P0/P4 实现照此。

## 关于"精简"

本轮把"精简"做成**清理冲突 + 统一术语**,**未删减设计依据(rationale)**。理由:那些 why/不变量说明对写实现有用,且文档随时可改——真正冗余的部分等代码落地、边界清晰后再合并更安全。

## 新增

- `refactor/README.md`:重构总纲(目标 / 现状结构 / 目标结构 / 8 阶段任务 / 工作流程 / 免责声明)。
- `refactor/TODO.md`:分阶段活清单。
- `refactor/changes/0001-...md`:本篇。

## 待办 / 下一步

- 进 **P0**:落 `core/enums.py` + `core/domain.py`(带 uid 等新字段),并把 `World.checkout/commit` 的目标房解析表落成代码。
- 校验项:实现 `commit` 时确认房间增/删/替换三路都覆盖;`checkout` 对纯大厅 `Connect`(nick 不在 users)的无房分支。

## 追加(同一会话,强化工作流程 + git)

按反馈强化了总纲的工作规约,并补了 git 使用:

- `refactor/README.md` §0:强调**写代码时文档大概率与现实不符,这是常态**——当场改文档和计划,别硬凑代码;明确「讨论的问题 + 结论也要记并落回文档」。
- `refactor/README.md` §5:确立 **「变更记录先行」**——动代码前先在 `changes/NNNN` 写「打算改什么」,完成后回填「实际改了什么/为什么/坑」;讨论产物是「更新后的文档」而非散落对话;收工同步 TODO + 提交(引用记录编号)。
- `dev.md`:新增 **「Git 使用」** 一节——主干 `develop`、提交信息引用 `changes/NNNN`、代码与文档同提交、秘密/`.env` 绝不进 git、日常流程;并在「约定」加第 5 条。

### 实操中修正的两条 git 规则(讨论 → 改文档)

实际跑了一遍 git 提交流程,据反馈修正了 `dev.md` 的 git 规约:

1. **分支模型从简**:原写「按重构阶段强制开分支」,但本仓库是**单人项目、只有 `develop` 一条分支**,强制开分支徒增合并开销。改为**默认直接提交 `develop`,只在大/有风险改动或想留 PR 时才开分支**。
2. **提交信息一律全英文**(标题/正文/trailer);中文只留给设计文档与 `changes/` 记录。同 [log.md](../../log.md)「日志英文」。

### git 落地(收尾)

- 首次把 `service/docs/` 纳入版本控制并推上 `origin/develop`(`fea47f3..338e6b1`)。
- 认证:`credential.helper store` + 浏览器授权(Git Credential Manager),凭证已缓存,后续推送免重复登录。`dev.md` 已补「认证(一次性设置)」小节。

> 本篇是「搭重构脚手架 + 文档自洽」这一个工作单元的完整账本,故未另起 0002。下一篇 `0002` 起按「先写意图再动手」用于真正的代码阶段(P0)。
