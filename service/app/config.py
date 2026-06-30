# 基础设施配置(单一事实源 = service/.env,见 config.md / dev.md「两套配置文件」)。
#
# 与游戏可调参数 app/gameconfig.py(poker.env,另一轨)分开:那是「换房/换策略想调的数」,这是
# 「DATABASE_URL / 未来 JWT 等基础设施/密钥」。
#
# 默认哲学(与 gameconfig 相反,有意):
#   - gameconfig 字段**无默认** → 缺值启动即崩(游戏参数必须显式)。
#   - 本模块 DATABASE_URL **有安全 dev 默认**(None → 消费方套本地 sqlite)→ 免 .env 也能跑迁移/dev(dev.md)。
#   - **但未来密钥(JWT_SECRET,P5)必须无默认(fail-closed)**:密钥缺失应启动即拒,不能偷偷跑一个空密钥。
#
# headless 约束(alembic 也 import 本模块的 settings 解析 DATABASE_URL,见 alembic/env.py):
#   P5 给 Settings 加必填字段时,**必须给默认或拆到独立 settings 类**,以保 alembic 在无该密钥时仍能跑迁移
#   (迁移是基础设施工具,只需库 URL,不应被应用密钥阻塞)。现仅 DATABASE_URL(有默认)→ 完全 headless-safe。

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).parent.parent  # service/ 目录;.env 归此(见 dev.md);锚绝对路径,CWD 无关


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里其它/未来键不报错
        case_sensitive=False,
    )

    # 数据库连接串(env > .env > 消费方方言默认)。缺省 None → engine.py 套 sqlite+aiosqlite、alembic 套 sqlite
    # (同一本地 ./poker.db,免 .env 也能跑)。生产设 postgresql+psycopg://…(psycopg3 同步/异步同 URL)。
    DATABASE_URL: str | None = None


# 启动期单例:import 即建。DATABASE_URL 有默认 → 不会因缺 .env 崩(alembic 也安全 import,见上 headless 约束)。
settings = Settings()
