# 配置规范:不要硬编码可调参数

## 一句话规则

任何「日后可能想调的数」都不许写成字面量散落在业务代码里,反面例子:`15`、`90`、`asyncio.sleep(0.5)`、`buy_in <= 1000`。这些数必须有名字、有类型、有取值边界,并集中声明;调参时只动配置文件,不动代码。

判断标准:换个房间、换个运营策略,会不会想改它?

- 会 → 进配置。
- 永远不会,比如 `len(suits) == 4`、`HOLE_CARDS == 2` → 属于规则常量,可以留在代码里,但也要写成具名常量,不能是裸数字。

## 单一事实来源:`poker.env` + 强类型 Settings

三条规矩:

1. 值只写在 `poker.env`,它是唯一事实来源。
2. 用 pydantic-settings 声明类型,落点是 [app/gameconfig.py](../app/gameconfig.py)。字段不写代码内默认值——缺了就启动报错。
3. 业务代码引用 `gameconfig.XXX`,不写字面量。

```python
# gameconfig.py —— 声明 + 取值边界(边界也是配置的一部分)
class GameConfig(BaseSettings):
    # —— 计时器 ——
    ACTION_TIMEOUT: float   = Field(ge=5, le=120)    # 行动倒计时(秒;float 对齐 timer.py 的 timeout_s/fire_at)
    LIVENESS_TIMEOUT: float = Field(ge=30, le=600)   # 断线占座/重连窗口(秒;同上 float)
    TIMER_TICK_MS: int    = Field(ge=100, le=2000)   # 扫描周期(毫秒)
    # …盲注 / 买入 / 房间 / delayDB / 日志 等既有与新增字段
```

```ini
# poker.env —— 改这里就够了,不碰任何 .py
ACTION_TIMEOUT=15
LIVENESS_TIMEOUT=90
TIMER_TICK_MS=500
```

```python
# 业务代码 —— 只引用,不内联
from app import gameconfig
class Timer:
    TICK = gameconfig.TIMER_TICK_MS / 1000
```

这套规则把以下参数全部收编为配置项:[timer.md](timer.md) 的 `ACTION_TIMEOUT` / `LIVENESS_TIMEOUT` / `TICK`、[db.md](db.md) 的 `DB_FLUSH_*`、[log.md](log.md) 的 `LOG_*`。

**当前状态:已落地**(0042,见 [changes/0042](refactor/changes/0042-config-consolidation.md))。[app/gameconfig.py](../app/gameconfig.py) 现在已经是 `GameConfig(BaseSettings)`:字段无代码默认值,用 `Field(ge=/le=/gt=)` 声明边界,`LOG_LEVEL` / `LOG_FORMAT` 用 `Literal` 收敛取值,缺值或越界启动即抛 `ValidationError`。

落地时有两处细化。

**其一,env 分两层加载**:`env_file=(app/poker.env.example, app/poker.env)`,后者覆盖前者,文件缺失则静默跳过。

- `poker.env.example` 提交进 git,作 canonical 基线,新检出的仓库或 CI 没有本地 `poker.env` 也能跑;`poker.env` 被 gitignore,只放本地覆盖。
- 值仍然全在受版本控制的 example 文件里,不在代码里;「缺字段启动即报错」对真正的缺失仍然生效,即 example、`poker.env`、OS env 三处都没有的情况。
- 路径锚定 `app/` 目录,用 `Path(__file__).parent`,不依赖 CWD。

**其二,访问接口不变**:业务代码仍然写 `from app import gameconfig` 然后 `gameconfig.ACTION_TIMEOUT`,不必写 `gameconfig.config.XXX`。这靠模块级 `__getattr__` 委托给单例 `config` 实现。

相关落地:

- `SetSmallBlind`/`SetBuyIn` 的盲注与买入上下限(`MIN/MAX_SMALL_BLIND`、`MIN/MAX_BUY_IN`)已随该命令补入(0043,见 [changes/0043](refactor/changes/0043-room-config-commands.md)),由 shell 在进 reduce 前防护,因为 core 不 import config。
- 基础设施的 `DATABASE_URL` 收编进另一轨 `app/config.py`(0045,见 [changes/0045](refactor/changes/0045-infra-config.md) + [dev.md](dev.md)「两套配置文件」)。JWT 等随 P5 处理。
- 默认值哲学分三档:游戏参数(gameconfig)无默认,缺值即崩;基础设施 `DATABASE_URL` 有安全的 dev 默认,缺省是 sqlite,不配 `.env` 也能跑;未来的密钥(JWT)必须无默认,fail-closed。
- 原型 `app/pokertable/gameconfig.py` 已于 0027 拆除。

## 新增一个可调参数 = 改三处,只有一处是「值」

1. `GameConfig` 加字段:名字 + 类型 + `Field(ge=…, le=…)` 边界。
2. `poker.env` 给值(`poker.env.example` 同步加一行)。
3. 代码里用 `gameconfig.字段` 引用。

调参时只动第 2 步。

## 约定细则

- **命名带单位**。`ACTION_TIMEOUT` 是秒,`TIMER_TICK_MS` 是毫秒。别让人猜 `TIMEOUT=90` 到底是秒还是毫秒。
- **边界即配置**。`MIN_SMALL_BLIND`、`MAX_BUY_IN` 这类上下限本身也是可调参数。业务校验写 `if x > gameconfig.MAX_BUY_IN`,不写 `if x > 1000`。
- **按域分组**。计时器、盲注、买入、房间、delayDB、日志各自归类并加注释。字段多了可以拆成 `TimerConfig` / `TableConfig` / `DBConfig` 子模型,仍由 env 驱动。
- **不写代码内默认值**。强制 env 给值,启动即校验。唯一例外是纯派生值,比如 `TICK = MS/1000`,由配置算出,不算硬编码。
- **真常量也要具名**。`HOLE_CARD_COUNT = 2` 之类,杜绝裸数字。

## 反例 → 正例

```python
# ✗ 硬编码,散落,改一处要全局搜
await asyncio.sleep(0.5)
if buy_in > 1000: ...

# ✓ 具名、集中、改 env 即可
await asyncio.sleep(gameconfig.TIMER_TICK_MS / 1000)
if buy_in > gameconfig.MAX_BUY_IN: ...
```
