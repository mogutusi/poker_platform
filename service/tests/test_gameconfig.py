# gameconfig 配置收编(0042):env 驱动 + 无代码默认 + Field 边界 + 模块 __getattr__ 委托。
# 不验业务取值(那些随 poker.env.example 漂移),只验「机制」:加载成功、边界拒非法、缺字段即崩、接口透传。

import pytest
from pydantic import ValidationError

from app import gameconfig
from app.gameconfig import GameConfig


def _valid_kwargs() -> dict:
    # 一份合法全字段入参,用于「单改一处使其越界」式 mutation 测;不读 env(_env_file=None 关闭 env 加载)。
    return dict(
        ACTION_TIMEOUT=15.0,
        LIVENESS_TIMEOUT=90.0,
        TIMER_TICK_MS=500,
        INBOX_MAX=1024,
        OUTBOUND_MAX=256,
        ERROR_DETAIL_MAX_LEN=200,
        ROOM_CHAT_MAX_TEXT_LEN=500,
        ROOM_CHAT_RATE_BURST=5.0,
        ROOM_CHAT_RATE_PER_SEC=1.0,
        ROOM_CHAT_HISTORY_SIZE=50,
        DM_MAX_TEXT_LEN=1000,
        DM_RATE_BURST=5.0,
        DM_RATE_PER_SEC=1.0,
        LOG_LEVEL="INFO",
        LOG_FORMAT="console",
        LOG_FILE="",
        DB_FLUSH_INTERVAL_MS=500,
        DB_WRITE_MAX_RETRY=10,
        DB_DRAIN_TIMEOUT_MS=5000,
        DM_READ_RETENTION_SECONDS=604800,
        DM_CLEANUP_INTERVAL_SECONDS=3600,
        MIN_SMALL_BLIND=1,
        MAX_SMALL_BLIND=100000,
        MIN_BUY_IN=1,
        MAX_BUY_IN=100000000,
        PWD_HASH_ROUNDS=100000,
        WS_FRAME_MAX_BYTES=65536,
        SESSION_TTL_SECONDS=3600,
        REST_FRAME_MAX_BYTES=65536,
        REST_REPLAY_WINDOW=64,
        LOGIN_REPLAY_WINDOW_SECONDS=120,
        LEADERBOARD_DEFAULT_LIMIT=20,
        LEADERBOARD_MAX_LIMIT=100,
        HANDS_DEFAULT_LIMIT=50,
        HANDS_MAX_LIMIT=200,
        DEV_ROOM="dev",
        DEV_SMALL_BLIND=1,
        DEV_BUY_IN=100,
        DEV_SEATS=6,
        DEV_USERS=("alice", "bob"),
        DEV_START_POINTS=1000,
        DEV_PASSWORD="devpass123",
        DEV_KUSER="00112233445566778899aabbccddeeff",
    )


def _build(**overrides) -> GameConfig:
    # _env_file=None:不读 poker.env(.example),纯用 kwargs,边界测才可控。
    return GameConfig(_env_file=None, **{**_valid_kwargs(), **overrides})


def test_loads_from_example_baseline():
    # 提交的 poker.env.example 让模块单例无 env 也能建(新检出/CI 即可跑);__getattr__ 透传。
    assert gameconfig.ACTION_TIMEOUT == gameconfig.config.ACTION_TIMEOUT
    assert isinstance(gameconfig.ACTION_TIMEOUT, float)
    assert gameconfig.DEV_USERS == tuple(gameconfig.config.DEV_USERS)


def test_module_getattr_unknown_raises():
    with pytest.raises(AttributeError):
        _ = gameconfig.DOES_NOT_EXIST


def test_valid_kwargs_build_ok():
    cfg = _build()
    assert cfg.ACTION_TIMEOUT == 15.0
    assert cfg.LOG_FORMAT == "console"


@pytest.mark.parametrize(
    "field, bad",
    [
        ("ACTION_TIMEOUT", 4),  # ge=5
        ("ACTION_TIMEOUT", 121),  # le=120
        ("TIMER_TICK_MS", 99),  # ge=100
        ("ROOM_CHAT_RATE_PER_SEC", 0),  # gt=0
        ("DM_RATE_PER_SEC", 0),  # gt=0
        ("DEV_SEATS", 1),  # ge=2
        ("DEV_SMALL_BLIND", 0),  # ge=1
        ("DB_WRITE_MAX_RETRY", 0),  # ge=1
        ("MIN_SMALL_BLIND", 0),  # ge=1
        ("MIN_BUY_IN", 0),  # ge=1
        ("MAX_SMALL_BLIND", 0),  # ge=1
        ("MAX_BUY_IN", 0),  # ge=1(四个房配上下限字段均有 ge=1 拒 0 的护栏)
        ("PWD_HASH_ROUNDS", 0),  # ge=1(rounds<1 会退化成不迭代 → 护栏拒)
        ("WS_FRAME_MAX_BYTES", 255),  # ge=256
        ("WS_FRAME_MAX_BYTES", 1048577),  # le=1048576
        ("SESSION_TTL_SECONDS", 59),  # ge=60
        ("SESSION_TTL_SECONDS", 86401),  # le=86400
        ("REST_FRAME_MAX_BYTES", 255),  # ge=256
        ("REST_REPLAY_WINDOW", 0),  # ge=1(窗宽 0 = 一切请求都「太旧」,配置层拒)
        ("LOGIN_REPLAY_WINDOW_SECONDS", 0),  # ge=1(窗 0 = 一切登录包都过期,配置层拒)
    ],
)
def test_field_bounds_reject(field, bad):
    with pytest.raises(ValidationError):
        _build(**{field: bad})


def test_log_level_literal_rejects_unknown():
    with pytest.raises(ValidationError):
        _build(LOG_LEVEL="TRACE")


def test_missing_field_raises_no_silent_default():
    # 无代码默认:漏给一个非默认字段 → 启动即 ValidationError(config.md「缺了启动即报错」)。
    kwargs = _valid_kwargs()
    del kwargs["ACTION_TIMEOUT"]
    with pytest.raises(ValidationError):
        GameConfig(_env_file=None, **kwargs)


def test_dev_users_requires_nonempty():
    with pytest.raises(ValidationError):
        _build(DEV_USERS=())


def test_dev_kuser_must_be_16_byte_hex():
    # DEV_KUSER 须是 32 位小写 hex(=16B SM4 密钥);非 hex / 长度不符 → 拒(启动即崩,不偷偷跑坏密钥)。
    with pytest.raises(ValidationError):
        _build(DEV_KUSER="not-hex")
    with pytest.raises(ValidationError):
        _build(DEV_KUSER="00112233")  # 太短(8 hex = 4B)
