# 配置规范:不要硬编码可调参数

## 一句话规则

**任何「日后可能想调的数」都不许写成字面量散落在业务代码里**(如 `15`、`90`、`asyncio.sleep(0.5)`、`buy_in <= 1000`)。它们必须有名字、有类型、有取值边界,集中声明,改的时候**只动配置文件,不动代码**。

判断是不是「可调参数」:**换个房间/换个运营策略会不会想改它?** 会 → 进配置。永远不会(`len(suits) == 4`、`HOLE_CARDS == 2`)→ 规则常量,可留代码里,但也写成具名常量而非裸数字。

## 单一事实来源:`poker.env` + 强类型 Settings

沿用本项目已确立的模式(别另起炉灶):

1. **值只写在 `poker.env`**(是唯一事实来源)。
2. **用 pydantic-settings 声明类型**,见 [gameconfig.py](../app/pokertable/gameconfig.py)。关键约定:**字段不写代码内默认值** → 缺了就启动即报错,从源头杜绝「代码里偷偷藏了个默认 15」。
3. **业务代码引用 `gameconfig.XXX`**,永远不写字面量。

```python
# gameconfig.py —— 声明 + 取值边界(边界也是配置的一部分)
class GameConfig(BaseSettings):
    # —— 计时器 ——
    ACTION_TIMEOUT: int   = Field(ge=5, le=120)      # 行动倒计时(秒)
    LIVENESS_TIMEOUT: int = Field(ge=30, le=600)     # 断线占座/重连窗口(秒)
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
from app.pokertable.gameconfig import gameconfig
class Timer:
    TICK = gameconfig.TIMER_TICK_MS / 1000
```

> 这把 [timer.md](timer.md) 的 `ACTION_TIMEOUT` / `LIVENESS_TIMEOUT` / `TICK`、[db.md](db.md) 的 `DB_FLUSH_*`、[log.md](log.md) 的 `LOG_*` 全部收编为配置项。

## 新增一个可调参数 = 改三处,只有一处是「值」

1. **`GameConfig` 加字段**:名字 + 类型 + `Field(ge=…, le=…)` 边界。
2. **`poker.env` 给值**(以及 `poker.env.example` 同步加一行)。
3. **代码里 `gameconfig.字段` 引用**。

调参时只动第 2 步。

## 约定细则

- **命名带单位**:`ACTION_TIMEOUT`(秒)、`TIMER_TICK_MS`(毫秒)。别让人猜 `TIMEOUT=90` 是秒还是毫秒。
- **边界即配置**:`MIN_SMALL_BLIND` / `MAX_BUY_IN` 这种上下限本身也是可调参数,业务校验写 `if x > gameconfig.MAX_BUY_IN`,不写 `if x > 1000`。
- **按域分组**:计时器、盲注、买入、房间、delayDB、日志各自归类加注释;字段多了考虑拆 `TimerConfig` / `TableConfig` / `DBConfig` 子模型,**仍由 env 驱动,业务代码始终引用对应 settings 对象**。
- **不写代码内默认值**:强制 env 给值,启动即校验。例外:纯派生值(`TICK = MS/1000`)可由配置算出,不算硬编码。
- **真常量也具名**:`HOLE_CARD_COUNT = 2` 之类,杜绝裸数字。

## 反例 → 正例

```python
# ✗ 硬编码,散落,改一处要全局搜
await asyncio.sleep(0.5)
if buy_in > 1000: ...

# ✓ 具名、集中、改 env 即可
await asyncio.sleep(gameconfig.TIMER_TICK_MS / 1000)
if buy_in > gameconfig.MAX_BUY_IN: ...
```
