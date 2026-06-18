# 重构 TODO(活清单)

> 规则见 [README.md](README.md) §5:每次收工**勾掉完成项 / 补新发现项**,并在 [changes/](changes/) 留一篇变更记录。
> 这是计划本身,**可以改**——发现顺序不对、任务拆错,直接调整。

图例:`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

---

## P0 · 基线(数据类型 + 工作副本 API)

- [x] `core/enums.py`:四套状态枚举 + `USER_STATUS_TRANSITIONS` 合法转移表(从现 [enums.py](../../app/pokertable/enums.py) 迁移) — 0002
- [x] `core/domain.py`:`World/Room/Hand/Player/Seat/UserState` dataclass(含 `UserState.uid`、`Hand.epoch/seq/start_time`、`Seat.in_game_points/new_here`、`Room.entry_vote/waive_entry_for`) — 0002(另含 `Hand.last_raise_size`、`Player.has_acted`、`Seat.wait_for_big_blind`、`Room.leaving`、`EntryVote`、`core/cards.py`)
- [x] `core/commands.py`:Command 全集,统一 `origin: str | None`,**不带 room**(`JoinRoom(room, uid, loaded)` 例外带 room) — 0002
- [x] `core/events.py`:`Broadcast(room,msg)`/`Personal(nick,msg)`/`Persist(payload)`/`TurnChanged`/`ClearAction` — 0002
- [x] `core/errors.py`:`ErrorCode` 枚举 + `Err(code, detail)` — 0002
- [x] `shell/world.py`:`checkout(world, cmd)` 按命令类型解析目标房(表见 [storage.md](../storage.md));`commit(world, work)` 处理房间增/删/替换 + users 表替换 — 0002(模块级函数而非 World 方法,见 0002 偏离记录)

## P1 · core 规则(主力,纯单测)

- [x] `core/deck.py`:`SystemRandom` 洗牌 + treys `Evaluator` 单例 — 0007
- [~] `core/rules/blinds.py`:定庄/盲位/heads-up、入局「付盲即玩 / 等大盲免费」、免盲投票 — 0008 落地定位 + 下盲(①.1-①.5);入局资格 established/付盲即玩/bootstrap/尊重 waive 快照(①.6/①.11)随 0010 `_start_hand` 落地;**等大盲再入局时机 + 躲盲被堵(①.7-①.10)+ 免盲投票(①.12-①.15)待 0011**
- [x] `core/rules/betting.py`:三动作校验、min-raise/重开、`street_closed` 谓词(`has_acted`)— 0007(另含 `settle_street`、`next_active_position`)
- [x] `core/rules/sidepot.py`:退还未叫注 → 分层削池 → 判池 + 奇数零头 — 0007
- [~] `core/reduce.py`:顶层 `match` + 各 helper(开局/动作/推进/摊牌/结束/连接/断线/超时/清理/买入/入座/状态/聊天/投票)— 0010 落地 `_start_hand`(开局);0011 落地 `_player_action` + 街推进/摊牌/边池结算/手牌记录(`core/records.py`)+ born-all-in runout(接住 0010 §6);**投票/连接/断线/超时/清理/买入/入座/状态/聊天/lobby + 局中离桌(rules.md ④)簇待后续**
- [~] `tests/core/`:按 [rules.md](../rules.md) 编号转穷举单测;守恒 + 隐私断言默认开 — 0007 落地 ②/③ 穷举(deck/betting/sidepot 34 测试,共 58);0008 落地 ① 定位/下盲穷举(blinds 7 测试,共 65);0010 落地 ① 开局 reduce 集成(test_start_hand 22 测试,共 88;含自 review 修复:bootstrap 看整桌/防躲盲、短牌堆守 Err、事件顺序/分支可分辨断言);0011 落地 `_player_action` 编排集成(test_player_action 12 测试 + born-all-in 改判,共 100;动作校验臂/街内换人/preflop 大盲选择权/多街推进/摊牌+边池还座/无摊牌结束/all-in 跑公共牌/守恒/隐私);等大盲/投票/局中离桌集成待后续

## P2 · shell 骨架

- [ ] `shell/gameloop.py`:`inbox` 串行 → checkout → reduce → commit/discard → dispatch(只 `put_nowait`)
- [ ] `shell/dispatch.py`:Event 路由(Broadcast 容错销毁房;B 组同步调 Timer)
- [ ] `tests/shell/`:工作副本回滚(失败 world 未动)、跨命令隔离

## P3 · 连接层

- [ ] `shell/connection.py`:`ConnectionManager`(register/unregister/is_current/get/rename)、`Connection`、`SecureChannel`
- [ ] `shell/receiver.py`:握手鉴权 → 登记(顶替)→ 起 Sender → `Connect` → 收帧循环 → 退出清理
- [ ] `shell/sender.py`:per-connection 出站,严格保序,慢客户端丢连
- [ ] `shell/timer.py`:`_action`(room 键)+ `_liveness`(nick 键)、staleness 由 reduce 兜
- [ ] `tests/shell/`:顶替身份判定、重连 `StateSnapshot`、队列满丢连

## P4 · delayDB + DB 模型

- [ ] `shell/persist.py`:`WriteBuffer`(双缓冲 swap)+ `PersistWriter`(先 swap 后 await)+ `to_orm`
- [ ] `db/` 模型:`User`(加 uid/salt/rounds/K_user 字段)、`HandRecord` 对齐 `HandRecordWrite`(uid/initial/final/pot)
- [ ] Alembic 迁移:密码哈希 `salt$rounds$digest`、K_user 双钥、手牌记录对齐(见 [dev.md](../dev.md))
- [ ] `tests/shell/`:覆盖/追加/回灌「更新者优先」/drain

## P5 · 鉴权信道

- [ ] 密码哈希:`salt$rounds$digest` + `compare_digest` + 数据迁移脚本
- [ ] 登录握手:`/user/login` SM4 护住密码、返回 session + JWT
- [ ] 逐帧加密:`SecureChannel` 入站「先验 seq → 验 MAC → 才解密」、出站加密
- [ ] `K_user` 双钥 + 每周轮换任务 + 版本/宽限
- [ ] `tests/crypto/`:MAC 拒伪 / seq 拒重放 / 先验后解 / IV 不复用

## P6 · wire codegen

- [ ] `wire/`:`ClientMessage`/`ServerMessage` 可辨识联合(`type` 字面量、扁平、snake_case)、隐私 `field_serializer` 隐藏底牌
- [ ] codegen:`pydantic2ts` + `openapi-typescript`,产物给前端;进 CI / pre-commit
- [ ] 前端:删手写 `types/poker.ts`,改用生成产物 + 实现加密帧

## P7 · 大厅 / 查询 / 聊天

- [ ] lobby:静态预置 `ROOMS`、`JoinRoom`/`LeaveRoom`、`GET /lobby/rooms`
- [ ] REST:leaderboard / hands(分页游标)/ profile(改昵称仅大厅 + `conns.rename`)
- [ ] messaging:房聊走 reduce + **shell 内存环形缓冲**(`FetchRoomChat` 拉历史,不落库);私聊走 shell 路由 + **未读收件箱**(`DMWrite` 事件写 / `DMReadCursorWrite` 状态写 / 完整已读回执 / PersistWriter 保留清理);限速在 shell。设计见 [messaging.md](../messaging.md)「持久化与离线送达」+ changes/0012
- [ ] presence:只读聚合 API

## P8 · 收尾

- [ ] `shell/lifespan.py`:启动正序 / 关闭反序 drain(超 `DB_DRAIN_TIMEOUT_MS` 落 CRITICAL)
- [ ] 日志:GameLoop 边界审计 + 脱敏红线(底牌/密钥不进日志)
- [ ] 配置收编:所有可调参数进 `gameconfig`,`poker.env` + `*.example` 同步
- [ ] 前端联调 + 端到端冒烟

---

## 持续项(随时回看)

- [ ] 文档与实现漂移时**改文档**并在 changes/ 记录
- [ ] 新增可调参数 → 进 `gameconfig` + env + example(不留裸字面量)
- [ ] 新增持久化实体 → 归「状态写 / 事件写」,不新开通道
