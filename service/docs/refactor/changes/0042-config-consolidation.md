# 0042 · 配置收编:gameconfig → pydantic-settings + poker.env.example

日期:2026-06-29 · 范围:`app/gameconfig.py`(带默认值常量 → `GameConfig(BaseSettings)` 无代码默认 + `Field` 边界 + 模块 `__getattr__` 保持 `gameconfig.XXX` 访问)、`app/poker.env.example`(新建,提交)、`config.md`/`dev.md`(收编落地)、`TODO.md`。落地 [config.md](../../config.md) P8「配置收编」目标形态(env 单一事实源 + 无代码默认 + 取值边界)。

## 背景 / 为什么

[config.md](../../config.md) §当前状态明记:D 阶段(0018/0019)`app/gameconfig.py` 用「带默认值的具名常量」,满足规范一半(具名 / 集中 / 不散落字面量),缺另一半「env 单一事实源 + 无代码默认」。执行序(TODO「硬化 → 日志/配置收编 → 国密(最后)→ 收尾」):硬化(0031)、日志(0032)已落,**配置收编是国密前的最后一块工程收编**,且是 `SetSmallBlind`/`SetBuyIn`(需买入/盲注上下限配置)与 `DM_*` env 化的前置。本批把 gameconfig 转成规范目标形态。

## 关键设计决策(批判性,与 config.md 对齐)

1. **保持 `gameconfig.XXX` 访问接口,零调用点改动(模块 `__getattr__` 委托单例)**。config.md 的示例与现有 44 处调用点都是 `from app import gameconfig` → `gameconfig.ACTION_TIMEOUT`。pydantic-settings 要求字段挂在 `BaseSettings` 实例上;为不破坏这一接口、不churn 12 文件,模块建单例 `config = GameConfig()` 后用 PEP 562 模块级 `__getattr__(name) → getattr(config, name)` 把字段名透到模块属性。**好处**:接口与文档逐字一致、调用点零改、启动期(import 时建单例)即做校验;**代价**:静态类型器看不到字段类型(本项目无严格 mypy,运行时正确性由测试保证)。比改 50+ 调用点为 `config.XXX` 更小、更稳、更贴文档。

2. **无代码默认值 + `Field(ge=/le=/gt=)` 边界 + `Literal` 枚举校验**(config.md 铁律「字段不写代码默认 → 缺了启动即报错」)。每字段去掉 `= 15.0` 这类内联默认,改 `Field(ge=…, le=…)`;`LOG_LEVEL`/`LOG_FORMAT` 用 `Literal[...]` 收敛取值。缺值 → `ValidationError` 启动即崩,从源头杜绝「代码偷藏默认 15」。

3. **env_file 两层:提交的 `poker.env.example`(基线)+ 本地 `poker.env`(覆盖,gitignored)**。这是对 config.md「值只写在 poker.env(唯一事实源)」的**有意偏离**(本批落地、已回写 config.md):
   - **问题**:若只读 `poker.env` 且无代码默认,则新检出 / CI 无 `poker.env`(gitignored)时,**import gameconfig 即 `ValidationError`**——全测试在收集期崩。
   - **解**:`env_file=(<dir>/poker.env.example, <dir>/poker.env)`,后者覆盖前者,缺文件静默跳过(pydantic-settings 语义)。`poker.env.example` 已提交、永远在,提供全部基线值;`poker.env` 本地覆盖。
   - **为何不破规范本意**:规范真正要堵的是「**`.py` 里藏字面量默认**」——本方案 `.py` 零默认,值全在受版本控制、被 review 的 `poker.env.example`(=canonical 值,本就该带真实游戏参数,非密钥)。「缺字段启动即报错」对真正缺失(example 与 poker.env 与 OS env 都没有)仍生效。example 兼作基线是常见工程实践(committed baseline + local override),比「提交 poker.env 本身」更贴现有「两套配置文件 + *.example」约定(`poker.env` 仍 gitignored 留作本地/未来密钥)。

4. **env_file 路径锚定模块目录(`Path(__file__).parent`),不依赖 CWD**。pydantic-settings 默认按 CWD 解析 env_file;测试 / alembic / uvicorn 的 CWD 不保证是 `service/`。锚到 `app/` 目录(`poker.env(.example)` 按 config.md/dev.md 归 `service/app/`)使加载 CWD 无关。

5. **`DEV_*` 一并 env 化(含 `DEV_USERS` JSON 数组)**,与其余字段统一。dev 脚手架参数也是「测试时想调的数」(config.md 判据),不另眼相待;`DEV_USERS` 在 env 写 JSON `["alice",…]`(pydantic-settings 复杂类型按 JSON 解析)。一致性优先,example 文件兼作 dev 配置。

6. **本批不新增「买入/盲注上下限」字段**(YAGNI):`MIN_SMALL_BLIND`/`MAX_BUY_IN` 等无消费方(`SetSmallBlind`/`SetBuyIn` 未落地),此刻加 = 死配置。随该命令落地时再加(届时业务校验引用)。本批只把**现有**字段转 env 驱动。

7. **不动 `DATABASE_URL` / `app/config.py`**(基础设施配置,另一轨):engine.py / alembic 仍直读 `os.environ`(见 dev.md「两套配置文件」)。`app/config.py` 收编 `DATABASE_URL`/JWT 是独立小单元,留作后续(见待办)。本批聚焦游戏可调参数(gameconfig)。

## 打算改什么(开工前)

- `app/gameconfig.py`:重写为 `GameConfig(BaseSettings)`(`model_config = SettingsConfigDict(env_file=(_DIR/"poker.env.example", _DIR/"poker.env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False)`)+ 全字段无默认 + `Field` 边界 + `Literal` 收敛 + 单例 `config` + 模块 `__getattr__`。保留每字段中文行内注释(含单位/语义)。
- `app/poker.env.example`:逐字段给 canonical dev 值(= 现常量值);分组注释。提交。
- `config.md`:§当前状态 → 落地(指 0042),补两层 env_file(example=基线 / poker.env=覆盖)说明 + `__getattr__` 接口说明。
- `dev.md`:poker.env 表行更新(已落地、两层加载)。
- `TODO.md`:勾「配置收编」(注明 `app/config.py`/上下限字段为余项)。
- 测:跑全量,确认 import / 启动 / 376 测试无回归;补一条 gameconfig 边界/加载冒烟测(可选)。

## 实际改了什么

- **`app/gameconfig.py`**:整体重写为 `GameConfig(BaseSettings)`。`model_config = SettingsConfigDict(env_file=(_ENV_DIR/"poker.env.example", _ENV_DIR/"poker.env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False)`,`_ENV_DIR = Path(__file__).parent`。27 字段全无代码默认(除 `LOG_FILE=""` 显式允许空,注释说明非「藏默认」)、带 `Field(ge/le/gt/min_length)` 边界;`LOG_LEVEL`/`LOG_FORMAT` 用 `Literal` 收敛。模块末 `config = GameConfig()`(import 即建,缺值/越界当场崩)+ PEP 562 `__getattr__(name) → getattr(config, name)`,保持 `from app import gameconfig` → `gameconfig.XXX` 接口不变(44 处调用点 / 6 处 import **零改动**)。
- **`app/poker.env.example`**(新建,提交):27 字段 canonical dev 值(= 原常量值)+ 分组注释。`extra="ignore"` 容未来/异类键。
- **测**:`tests/test_gameconfig.py`(14):example 基线加载 + `__getattr__` 透传 + 未知名 AttributeError + 8 条 `Field` 边界拒(`ge/le/gt`)+ `LOG_LEVEL` `Literal` 拒非法 + 缺字段 `ValidationError`(无静默默认)+ `DEV_USERS` 非空。`_build(_env_file=None, **kwargs)` 关 env 加载、纯 kwargs,边界测可控。
- **文档**:`config.md`(§当前状态 → 落地 0042,补两层 env_file + `__getattr__` 接口 + 余项)、`dev.md`(poker.env 表行 + example 兼作加载基线说明)、`log.md`(LOG_* 已 env 化)、`messaging.md`(×2:DM/房聊阈值已 env 化)、`TODO.md`(配置收编勾掉 + 余项)。
- **未动**:wire(无协议改,`wire.gen.ts` 不变,codegen `--check` 干净)、core(纯同步不碰)、`DATABASE_URL`/`app/config.py`(基础设施另一轨,余项)、迁移。

390 全绿(376→390,+14);codegen 漂移守门通过(test_codegen_uptodate);core 无越层 import(未碰 core)。

## 自 review

方法:对照 [review.md](../../review.md) 逐维自审,最高风险面(env 加载正确性)**写脚本实证**;候选默认先反驳。

- **① 分层 / 不变量**:配置层改动,未碰 core(纯同步不变);`gameconfig` 仍只被 shell 引用,无新越层 import。`config` 单例 import 期建——读 env 文件是同步 IO,但发生在**模块导入 / 进程启动**(非 reduce 路径、非 core),合法(core 禁读墙钟/IO 指的是 reduce 运行期)。
- **④ 数据模型正确性(最高风险面 = env 加载)**:**实证**——(a) 27 字段全量 `model_dump` 逐一核对,值与原常量**逐字相等**、无行内注释污染;(b) **抓到并修一处真 bug**:`LOG_FILE=`(空值)后跟行内注释时,python-dotenv 把注释当值(其余非空字段行内注释正常剥离)——改为注释单独成行、`LOG_FILE=` 裸空,复测 `== ''`;(c) 两层覆盖实证:本地 `poker.env` 覆盖 example(`ACTION_TIMEOUT=99`/`DEV_USERS=["zoe"]` 生效)、未给的键回落 example(`TIMER_TICK_MS=500`)、缺 `poker.env` 静默跳过不崩。边界/`Literal`/缺字段均由新测覆盖(mutation:删字段 / 越界 / 非法枚举均 `ValidationError`)。
- **② 代码↔文档同步**:本批是**有意偏离 config.md 字面**(「值只写在 poker.env、单一事实源」→ 改为「example 提交基线 + poker.env 本地覆盖」),已在 config.md / dev.md **同次回写**并论证(决策 3）——文档与代码一致,无悬空。`__getattr__` 接口保持文档示例 `gameconfig.XXX` 逐字有效。
- **③ 文档↔文档一致**:log.md / messaging.md(×2)的「现 dev 常量、P8 env 化」前瞻语句已统一改「已随 0042 env 化」;changes/ 历史记录(0018/0032 等)保留不改(历史属实)。TODO 勾项 + 余项标注。
- **⑤ 规范**:每字段保留中文行内注释(含单位/语义);无裸字面量进 .py(值全在 env)；`__getattr__` 带注释讲「为什么」(保接口、PEP 562);`extra="ignore"` 注释说明(容异类键)。
- **⑥ 测试**:14 新测覆盖加载 / 边界 / 缺字段 / Literal / 委托 / 非空,且**实跑确认**(脚本核 27 字段值 + 覆盖回落 + 空值修复)。390 全绿。
- **⑦ 流程账本**:打算↔实际对照已记(LOG_FILE 空值 bug 是「实际」多出的修复);测计数 376→390;`poker.env.example` 已确认**可提交**(`git check-ignore` 空)、本地 `poker.env` **被忽略**(`git check-ignore` 命中);提交将引用 0042、全英文。

**对抗核实存活 / 采纳 / 驳回**:
- **确认 1(真 bug,采纳修)**:`LOG_FILE=` 空值吞行内注释 → 值变注释文本。实证捕获,改注释独立成行,复测通过。这正是「绿测前先实证最高风险面」抓到的——若不逐字段核值,该 bug 会让 `LOG_FILE` 指向一个中文注释路径、运行期 `setup_logging` 才炸。
- **驳回 1**:`extra="ignore"` 隐藏 env 键拼写错(typo'd 键静默无效)。驳:必要权衡——容许 env 文件含基础设施 / 未来键而不崩;且拼错的覆盖键回落 example 基线(行为安全,非崩)。记为已知取舍,非缺陷。
- **驳回 2**:`case_sensitive=False` + 读 OS env 可能被同名环境变量(如 CI 的 `LOG_LEVEL`)覆盖。驳:这是**期望的配置优先级**(OS env > 文件),pydantic-settings 标准语义,正是「运维可临时覆盖」诉求,非 bug。

## 待办 / 下一步

- `app/config.py`(基础设施 Settings:`DATABASE_URL` 等)收编 engine.py / alembic 直读 `os.environ` —— 独立小单元。
- `SetSmallBlind`/`SetBuyIn`(P1 余项)落地时,gameconfig 补 `MIN/MAX_SMALL_BLIND`/`MAX_BUY_IN` 上下限字段 + 业务校验引用。
- `DM_*`/`LOG_*`/`DB_*` 已随本批 env 化,后续只动 `poker.env`。
