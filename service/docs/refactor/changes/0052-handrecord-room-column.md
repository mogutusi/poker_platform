# 0052 · `HandRecord.room` 列 + `GET /hands?room=` 健壮过滤(写路径 + 迁移)

日期:2026-07-01 · 范围:`app/db/models.py`(HandRecord 加 `room` 列)、`app/core/records.py`(HandRecordWrite 加 `room`)、`app/core/reduce.py`(`_finalize_hand` 带 room)、`app/db/orm_persister.py`(INSERT 带 room)、新迁移 `alembic/versions/010d8e8a08d7_add_handrecord_room.py`、`app/db/queries.py`(`list_hands` room 过滤)、`app/rest/hands.py`(`?room=`)、测(5 文件涟漪 + 3 新 room 测)、`docs/rest.md`/`db-migrations.md`/`TODO.md`。兑现 [0051](0051-rest-hands.md) 明确推迟的 `room` 过滤。

## 背景 / 为什么

0051 落 `GET /hands` 时**显式推迟 `room` 过滤**:`HandRecord` 无 room 列,room 只在 `dedupe_key="room:seq"`,动态房名(0049)任意 → `dedupe_key LIKE` 受 LIKE 通配符/`:` 之扰**脆弱**。正确解 = 给 `HandRecord` 加独立 `room` 列,`WHERE room=?` 精确匹配。本批做这个「写路径 + schema」变更,并接上 `?room=`。

## 关键设计决策

1. **denormalize room 进列**:`room` 与 `dedupe_key` 冗余(后者含前者),但 dedupe_key 是**幂等键**(不宜拆解查询)、room 是**可查列**——分职。`HandRecordWrite` 加 `room`(required,`_finalize_hand` 从 `work.room_name` 取,该值函数顶已 assert 非 None);orm_persister INSERT 带 room;core 依旧只携带、不解析。
2. **迁移用 `--autogenerate` + 手调**(db-migrations.md 工作流):autogen 检出 `room` 列 + `ix_handrecord_room` 索引,但生成的 `nullable=False` **无 server_default** → 加到已有行的表会崩。**手改**加 `server_default=''`:0052 之前的历史手牌 room 记 `''`(哨兵,那时未记 room);ORM 之后一律显式写 room、不依赖该默认。**已实测**:插一条 pre-migration 行 → `upgrade` 后该行 room=`''`、列+索引就位;`downgrade -1` drop 列;`upgrade` 再加(round-trip 通)。
3. **列长 `String(128)`**:与 `dedupe_key`(String128,= "room:seq")一致——room < dedupe_key,不引入比现状更严的房名长度上限(现有长房名已被 dedupe_key String128 隐式兜;真正的房名长度校验属 0049 territory,本批不碰)。
4. **`room` 过滤 = 精确 `==`**:`list_hands(room=...)` → `WHERE HandRecord.room == room`,免 LIKE、免转义、免 `:` 歧义;可与 `user`/`before` 组合。
5. **测试用 `create_all` 天然含新列**(迁移是 schema 版本化/真库用);dev `DevShell.setup` 亦 `create_all`,新库直接有列。

## 打算改什么 / 实际改了什么

- **`app/db/models.py`**:`HandRecord` 加 `room: str = Field(max_length=128, index=True)`(dedupe_key 之后)。
- **`app/core/records.py`**:`HandRecordWrite` 加 `room: str`(dedupe_key 之后、start_time 之前,守 required 在 default 前)。
- **`app/core/reduce.py`**:`_finalize_hand` 构 `HandRecordWrite(..., room=work.room_name, ...)`。
- **`app/db/orm_persister.py`**:`_insert_hand_record` 的 `HandRecord(..., room=payload.room, ...)`。
- **`alembic/versions/010d8e8a08d7_add_handrecord_room.py`**(新,autogen + 手加 server_default):`add_column room` + `create_index ix_handrecord_room`;down_revision=`7ff9cb0a8db1`(接 DM 游标后)。
- **`app/db/queries.py`**:`list_hands(..., room=None, ...)` + `if room is not None: where(HandRecord.room == room)`。
- **`app/rest/hands.py`**:`get_hands(room=Query(None), ...)` 透传;模块头「room 过滤 0052 已做」。
- **测**:4 处 `HandRecordWrite` 测助手加 `room=`(test_persist/test_persist_writer/test_dispatch/test_orm_persister `_record`)+ test_hands `_seeded` HandRecord 加 room(r1/r1/r2)+ **3 新**(query room 过滤 / query room+user 组合 / route room)+ orm_persister 断言 `hr.room=="r1"` 落库。两处旧 route 直调测补 `room=None`(直 await 绕过 FastAPI Query 默认)。
- **docs**:rest.md §手牌历史(room 过滤转正)、db-migrations.md(新迁移一行)、TODO P7(hands 行去「room 待加」)。

459→462 全绿(+3 room 测:query room 过滤 / query room+user 组合 / route room;另涟漪修 5 处 HandRecordWrite 构造 + orm_persister room 落库断言 + test_hands 播种 room,均并入既有测不增计数);迁移 upgrade/downgrade/backfill 实测通;`gen_wire_ts --check` OK(未碰 wire)。

## 自 review

对照 review.md 逐维:

- **① 分层 / 不变量**:`room` 走 shell→core→db 与 dedupe_key 同路(core 只携带、不读钟/不解析);core 无新 import。money/隐私路径未动(room 是非隐私元数据)。工作副本回滚不涉。
- **② 代码↔文档同步**:rest.md room 过滤转正、db-migrations.md 记新迁移、TODO 去 defer 注 —— 与代码一致。
- **③ 文档↔文档一致**:0051 记录的「room 推迟」现由 0052 兑现,rest.md/TODO 同步去 defer;models.py 列注释 ↔ records.py ↔ 迁移三处 room 语义一致。
- **④ 数据模型**:`room` NOT NULL + index;迁移 server_default='' 处理既有行(实测 backfill);列长 128 与 dedupe_key 一致不引入新上限。**对抗自问「NOT NULL 加列会不会崩既有库」**→ 手加 server_default,实测 pre-migration 行 upgrade 后 room=''(存活=既有行安全)。
- **⑤ 规范合规**:迁移手改处有「为什么」注释(autogen 不完美 + server_default 语义);列/字段注释齐;无裸字面量。
- **⑥ 测试充分**:room 过滤(命中/另一房/无此房空)+ room×user 组合(alice 在 r1 命中、r2 空)+ route room + orm_persister room 落库断言;涟漪 5 处 HandRecordWrite 构造补齐。**迁移**:autogen 后**手动实测** upgrade(含既有行 backfill)/downgrade/re-upgrade round-trip(测试套用 create_all、不覆盖迁移,故人工验)。**未覆盖**:含 `:`/通配符的房名 room 过滤(现精确 `==` 天然免疫,无需测 LIKE 边界)。
- **⑦ 流程账本**:迁移文件随模型改同批 commit(db-migrations.md);打算↔实际一致;提交引用 0052、全英文。

**对抗核实**:自问 2——(a)「既有库加 NOT NULL 列崩?」→ server_default='' 实测 backfill(存活=安全);(b)「room 冗余 dedupe_key 会不会不一致?」→ 二者同一 `work.room_name` 源、同批构造,结构上一致;room 供查询、dedupe_key 供幂等,分职非冗余风险(存活=分职正当)。0 真 bug。

## 待办 / 下一步

- P7 余:profile(`GET /user/me` / `PATCH /user/nickname` / `PATCH /user/password`)依赖 **P5** 鉴权/密码哈希。
- REST → TS codegen(openapi,无 node 待解):REST DTO 前端类型生成另开一篇。
