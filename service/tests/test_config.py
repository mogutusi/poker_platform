# 基础设施配置收编(0045):app/config.Settings —— DATABASE_URL env 驱动 + 安全 dev 默认。
# 不验真实值(随本地 .env / 环境漂移),只验机制:缺省 None、env 覆盖、engine 方言回落。

import app.config
from app.config import Settings
from app.db import engine


def test_database_url_defaults_to_none_without_env(monkeypatch):
    # 无 env 变量、不读 .env 文件 → None(免 .env 也能跑:消费方套方言默认)。
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings(_env_file=None).DATABASE_URL is None


def test_database_url_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/poker")
    assert Settings(_env_file=None).DATABASE_URL == "postgresql+psycopg://u:p@h:5432/poker"


def test_engine_url_falls_back_to_async_sqlite(monkeypatch):
    # engine.database_url():DATABASE_URL 缺省 → 异步 sqlite 默认(注意是 +aiosqlite,非 alembic 的同步 sqlite)。
    monkeypatch.setattr(app.config.settings, "DATABASE_URL", None)
    assert engine.database_url() == engine.DEFAULT_DATABASE_URL
    assert engine.database_url().startswith("sqlite+aiosqlite")


def test_engine_url_uses_configured(monkeypatch):
    monkeypatch.setattr(app.config.settings, "DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    assert engine.database_url() == "postgresql+psycopg://u:p@h/db"


def test_engine_url_empty_string_falls_back(monkeypatch):
    # 空串(如 .env 里 `DATABASE_URL=`)falsy → 回落默认(`or` 兜 None 与 "")。
    monkeypatch.setattr(app.config.settings, "DATABASE_URL", "")
    assert engine.database_url() == engine.DEFAULT_DATABASE_URL


def test_env_file_read_when_no_env_var(tmp_path, monkeypatch):
    # 无 os.environ 时读 .env 文件值(0045 接 .env 的核心:文件确实被加载)。
    envfile = tmp_path / ".env"
    envfile.write_text("DATABASE_URL=sqlite:///from_file.db\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings(_env_file=str(envfile)).DATABASE_URL == "sqlite:///from_file.db"


def test_env_var_beats_env_file(tmp_path, monkeypatch):
    # 不变量 d:os.environ 优先于 .env 文件(pydantic-settings 源优先级;dev.md「os.environ 优先覆盖 .env」)。
    # 钉死该优先级——防未来改成「先 load_dotenv 再读」之类让文件反压 env 的回归。
    envfile = tmp_path / ".env"
    envfile.write_text("DATABASE_URL=sqlite:///from_file.db\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env:wins@h/db")
    assert Settings(_env_file=str(envfile)).DATABASE_URL == "postgresql+psycopg://env:wins@h/db"
