# 0024 · P4(一之一):delayDB 写缓冲双缓冲(状态写覆盖 / 事件写追加 / swap / 回灌更新者优先)

日期:2026-06-24 · 范围:`app/shell/persist.py`(`WriteBuffer` 桩 → 双缓冲实落)、`tests/shell/test_persist.py`(新)、文档同步(db.md / TODO / testing.md / connection.md / messaging.md —— 后三者随 `put_*→put` 重命名连带同步)。

## 背景 / 打算改什么

P1 余项收口(0023 等大盲落地)后,执行序进入**硬化阶段「delayDB / 背压 / 重连」**(见 [TODO](../TODO.md) 执行顺序)。delayDB 是架构核心不变量「内存权威 + 滞后落库」的兑现点,当前 [persist.py](../../../app/shell/persist.py) 还是 0018 的桩(单 list `put`/`snapshot`)。

按 [README §0](../README.md) 质疑粒度:P4 全量(双缓冲 `WriteBuffer` + `PersistWriter` 协程 + `to_orm` + `db/` ORM 模型 + Alembic 迁移 + lifespan drain + gameconfig 旋钮)是一大坨,且混了**纯同步数据结构**(缓冲)与**async + DB + 迁移**两类风险面。最干净的缝在二者之间:

- **`WriteBuffer` 双缓冲**是**纯同步数据结构**——状态写按键覆盖、事件写追加、`swap` 双缓冲取走清空、`requeue` 回灌「更新者优先」。[db.md](../../db.md) 明言这是「本模块唯一易错处」(覆盖 vs 追加、先 swap 后 await、回灌更新者优先)。它**脱离 DB/async 即可穷举单测**——正是架构「最大正确性风险先脱 IO 钉死」的同一智慧(P1 reduce 脱 shell 单测)。
- `PersistWriter`(async 消费者,先 swap 后 await)+ `to_orm` + `db/` 模型 + Alembic 是**另一类**(async + 真 DB + 迁移),需要 DB 基建,留下一篇。

**本篇(0024)只落 `WriteBuffer` 双缓冲 + 单测**,不碰 async/DB。

### 设计决策(开工前定)

1. **`put(payload)` 单入口 + 内部按类型分流**(而非 db.md 伪码的 `put_state(StateWrite)`/`put_append(AppendWrite)` 双方法 + 包装类)。理由:① dispatch 调用点 `self.persist.put(p)` **不动**(最小爆炸半径);② payload 自带识别字段(`PointsWrite.uid` / `HandRecordWrite.dedupe_key`),无需 `StateWrite`/`AppendWrite` 包装层,少一层 indirection;③ 「payload→是状态写还是事件写 + 覆盖键」这一映射集中在 `_state_key` 一处,不散进 dispatch。**这是对 db.md 伪码的简化偏离 → 同篇同步 db.md**(伪码标示意,字段/签名以代码为准,见 coding_principle 双向同步)。
2. **内部存储 = `_dirty: dict[StateKey, payload]` + `_appends: list[payload]`**。状态写按 `StateKey` 覆盖(同键只留最新),事件写逐条 append。`swap()` 返回 `(dirty, appends)` 并原子置空(双缓冲:返回的批次成为 PersistWriter 私有局部,期间新写进**新空缓冲**)。
3. **`StateKey = ("user", str(uid))`**(全 str 元组,匹配 db.md `tuple[str, ...]`;真 DB 主键在 `to_orm` 时取 `payload.uid` 原值,key 只为内存覆盖去重)。
4. **未知 payload 默认归事件写(追加)+ WARNING**(db.md「拿不准默认事件写」:覆盖一个本该追加的实体会静默丢数据,代价远高于多落几条)。
5. **保留 `snapshot()` + `__len__`**(向后兼容:`test_gameloop`/`test_dispatch`/`test_dev_smoke` 在用;`__len__`=dirty+appends 总数,`snapshot()`=全部 payload 只读视图)。
6. **不加 gameconfig 旋钮**:`DB_FLUSH_INTERVAL_MS`/`DB_FLUSH_MAX_BATCH`/… 是 `PersistWriter` 的参数,本篇无消费者,留下一篇随 PersistWriter 落(避免「未用配置」)。

## 实际改了什么

- **`app/shell/persist.py`**:`WriteBuffer` 桩 → 双缓冲实落。
  - 两桶:`_dirty: dict[StateKey, payload]`(状态写,同键覆盖)+ `_appends: list[payload]`(事件写,逐条追加)。
  - `put(payload)`:单入口,`_state_key(payload)` 分流(`PointsWrite`→`("user", str(uid))` 覆盖键;`HandRecordWrite`→None 追加;未知→None 追加 + WARNING)。
  - `swap() -> (dirty, appends)`:同步取走两桶并重绑空 `{}`/`[]`(双缓冲)。
  - `requeue(dirty, appends)`:状态写 `setdefault`(更新者优先)+ 事件写前插(`_appends[:0]=appends`)。
  - `is_empty()` + 向后兼容 `snapshot()`(全 payload 只读)/ `__len__`(dirty+appends 总数)。
  - `dispatch.py` 调用点 `self.persist.put(p)` **不动**。
- **`tests/shell/test_persist.py`**(新,12 测试)。
- **文档同步**:`db.md`「写缓冲」段重写为 `put` 单入口 + `_state_key` 分流(+ 0024 偏离注),`PersistWriter`/`requeue` 伪码对齐 payload-direct;`testing.md`/`connection.md`/`messaging.md` 把 `put_state`/`put_append` 机械重命名为 `put`(语义「状态写/事件写」改行内标注);`TODO` P4 标 `[~]`。

**偏离设计**:对 db.md 伪码的简化(`put(payload)` 单入口 + `_state_key` 内部分流,弃 `StateWrite`/`AppendWrite` 包装类 + `put_state`/`put_append` 双方法)——已同篇同步 db.md(见上「设计决策 1」)。无架构/不变量偏离。

## 测试

`tests/shell/test_persist.py`(12),**全量 241 绿**(0023 的 229 + 本篇 12)。覆盖:状态写同键覆盖(只留最新)、不同键并存、事件写逐条追加(同 dedupe_key 不内存去重)、状态/事件混存、`swap` 清空、**双缓冲隔离**(swap 后 `put` 不污染已取走批次)、`requeue` 更新者优先(旧值不盖新值)/ 缺键回灌生效 / 事件写前插、未知 payload 默认追加、`is_empty`、向后兼容 `snapshot`。守恒/隐私非本结构关注点(payload 已是脱敏快照值)。

四个具名 mutant 被钉死:`put` 恒追加 / `swap` 不清空 / `requeue` 用 `[]=` 而非 `setdefault` / appends `extend` 而非前插——任一变异都使对应测试红。

## 自 review(push 前对抗式 7 维)

> 跑了一轮多 agent 对抗式 7 维复审(refute-by-default + 综合)。**候选 8、确认 0、反驳 8,SAFE-TO-COMMIT,零正确性/规范/文档缺陷。**

- **① 正确性 / ④ 不变量**:逐项反驳——`swap()` 原子捕获双桶并重绑新 `{}`/`[]`,swap 后 `put` 只动新桶、交出批次不被改(payload 为 frozen dataclass,`requeue` 只读不改参);`setdefault` 保证更新者胜(旧 100 不盖新 250)、appends 前插保「先发生先落」;`str(uid)` 对 int 单射不碰撞;未知 payload 归 append + WARNING、不静默丢;`put` 纯同步无 `await`,契合 dispatch 不变量 3。
- **② 文档同步**:唯一候选(db.md 残留 `put_state/put_append`)证伪——working tree 两处 DM 引用均已为 `put`,残留仅在 git **index** 暂存副本(commit 前 `git add` 即消);db.md:32 是声明「不用」的偏离注;`testing.md`/`connection.md`/`messaging.md` 均已同步。
- **⑤ 规范**:`persist.py` 每字段/方法中文注释、无魔法值、命名对齐 db.md。
- **⑥ 测试充分**:四个 mutant 全被杀;断言基于 frozen dataclass 真 `==`,非同义反复;空桶 swap 路径平凡(`return {}, []`),无存活变异。
- **③/⑦**:守「唯一写库者 PersistWriter」「先 swap 后 await」红线;`__len__`/`snapshot` 向后兼容(`test_gameloop`/`test_dispatch`/`test_dev_smoke` 全绿)。

修后全量 **241 绿** + core 纯度通过。无确认项需修(候选全反驳);连带的 `put_*→put` 文档重命名已纳入「范围」。

## 待办 / 下一步

- **P4(二):`PersistWriter` 协程**(`run`:周期 `swap` → 短事务 UPSERT/INSERT;失败 `requeue` 整批;毒丸阈值;drain)+ `to_orm` + `db/` ORM 模型(`User` 加 `uid`/`salt`/`rounds`/`K_user`、`HandRecord`+`Participant` 对齐 `HandRecordWrite`)+ Alembic 迁移 + gameconfig DB 旋钮 + lifespan drain wiring。
- 私信两类写(`DMWrite`/`DMReadCursorWrite`)是写缓冲第二生产者(P7 messaging),落地时在 `_state_key` 加 `DMReadCursorWrite` 状态写分类。
