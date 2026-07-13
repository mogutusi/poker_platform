# 游戏可调参数(单一事实源 = poker.env,见 config.md / refactor/README §3)。
#
# 形态(0042 配置收编落地):`GameConfig(BaseSettings)`,字段无代码默认 + `Field` 边界 → 缺值启动即报错,
# 从源头杜绝「代码偷藏默认 15」。值全在 env 文件,改参数只动 env、不碰 .py(config.md)。
#
# 加载两层(后者覆盖前者,缺文件静默跳过):
#   1. poker.env.example —— 提交进 git 的基线(canonical dev 值,永远在 → 新检出/CI 即可跑)
#   2. poker.env         —— 本地覆盖(gitignored),按需调参
# 路径锚定本模块目录(不依赖 CWD;测试/alembic/uvicorn 的工作目录不保证是 service/)。
#
# 访问接口:`from app import gameconfig` → `gameconfig.ACTION_TIMEOUT`(经模块 __getattr__ 委托单例 config,
# 见文件末;保持 0018 起的调用接口不变)。与基础设施配置 app/config.py(DATABASE_URL/JWT…,另一轨)分开。

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_DIR = Path(__file__).parent  # app/ 目录;poker.env(.example) 归此(见 config.md / dev.md)


class GameConfig(BaseSettings):
    # env 两层:example(提交基线)→ poker.env(本地覆盖);锚到 app/ 绝对路径,CWD 无关。
    model_config = SettingsConfigDict(
        env_file=(_ENV_DIR / "poker.env.example", _ENV_DIR / "poker.env"),
        env_file_encoding="utf-8",
        extra="ignore",  # env 里多余键(如 DATABASE_URL)不报错——那是 app/config.py 的地盘
        case_sensitive=False,
    )

    # ── 定时器(见 timer.md)──
    ACTION_TIMEOUT: float = Field(ge=5, le=120)  # 行动倒计时(秒):轮到某人后多久没动作即投 Timeout(默认 check / fold)
    LIVENESS_TIMEOUT: float = Field(ge=30, le=600)  # 断线占座窗口(秒):断线后超此值投 Cleanup 退筹释座(0070:断线装表/重连拆表;观战者断线即清不经此窗);须 ≫ ACTION_TIMEOUT
    TIMER_TICK_MS: int = Field(ge=100, le=2000)  # Timer 扫描周期(毫秒):到点最多迟一个 tick

    # ── 背压(见 architecture.md「队列有界」)──
    INBOX_MAX: int = Field(ge=64, le=65536)  # GameLoop inbox 上限;满 = GameLoop 卡死(进程级 bug,落 CRITICAL)
    OUTBOUND_MAX: int = Field(ge=16, le=8192)  # 每连接 outbound 上限;满 = 慢客户端,丢连接 + Disconnect(不阻塞 GameLoop)
    ERROR_DETAIL_MAX_LEN: int = Field(ge=20, le=2000)  # 回发 ErrorMessage.detail 的最大字符数(截断 Pydantic 校验错误文本)

    # ── 房聊文本防护 / 限速(见 messaging.md;shell 进 reduce 前防护)──
    ROOM_CHAT_MAX_TEXT_LEN: int = Field(ge=1, le=10000)  # 房聊单条正文最大字符数;超则 Receiver 拒(MESSAGE_TOO_LONG)
    ROOM_CHAT_RATE_BURST: float = Field(ge=1, le=100)  # 令牌桶容量(突发上限):静默后最多连发几条
    ROOM_CHAT_RATE_PER_SEC: float = Field(gt=0, le=100)  # 令牌桶稳态补充速率(每秒令牌数 = 每秒可发条数)
    ROOM_CHAT_HISTORY_SIZE: int = Field(ge=0, le=1000)  # 每房内存环形缓冲保留的最近房聊条数(不落库;进/重进房经 FetchRoomChat 拉,见 messaging.md)

    # ── 私信 DM 文本防护 / 限速(见 messaging.md §私信;shell 进路由前防护)──
    DM_MAX_TEXT_LEN: int = Field(ge=1, le=10000)  # 私信单条正文最大字符数;超则 shell DM 路由拒(MESSAGE_TOO_LONG)
    DM_RATE_BURST: float = Field(ge=1, le=100)  # 私信令牌桶容量(突发上限):静默后最多连发几条
    DM_RATE_PER_SEC: float = Field(gt=0, le=100)  # 私信令牌桶稳态补充速率(每秒可发条数)

    # ── 日志(见 log.md)──
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]  # root 级别:DEBUG=全量审计 / INFO=业务里程碑 / WARNING+=异常路径
    LOG_FORMAT: Literal["json", "console"]  # "json"=结构化一行一条(生产,jq 过滤)/ "console"=人类友好单行(本地)
    LOG_FILE: str = ""  # 落地文件路径;空串=只写 stderr(dev 默认)。空串非「代码默认」——env 显式给空亦可,这里允许 env 缺省为空

    # ── delayDB 写回(见 db.md)──
    DB_FLUSH_INTERVAL_MS: int = Field(ge=50, le=10000)  # PersistWriter 落库周期(毫秒)= 同实体多次变更合并窗 = 积分落库最大滞后 / 崩溃窗口
    DB_WRITE_MAX_RETRY: int = Field(ge=1, le=100)  # 同批连续落库失败**达**此次数(总尝试数,非额外重试)→ 毒丸:丢批 + CRITICAL(别卡死后续,bug 信号)
    DB_DRAIN_TIMEOUT_MS: int = Field(ge=0, le=60000)  # 优雅关闭 drain 上限(毫秒):超时放弃未落写 + CRITICAL(进程要退,接受该窗口)
    DM_READ_RETENTION_SECONDS: int = Field(ge=0, le=31536000)  # 已读私信再留多久(秒)后清(默认 7 天);未读不受限、一直保活(见 messaging.md / db.md)
    DM_CLEANUP_INTERVAL_SECONDS: int = Field(ge=60, le=86400)  # PersistWriter 跑私信保留清理的周期(秒;默认每小时一趟)

    # ── 房间参数配置上下限(SetSmallBlind/SetBuyIn 的合法区间;shell 进 reduce 前防护,见 changes/0043)──
    MIN_SMALL_BLIND: int = Field(ge=1, le=1000000)  # 运行时改小盲的下限(≥1 → 大盲 = 2× ≥ 2)
    MAX_SMALL_BLIND: int = Field(ge=1, le=1000000)  # 运行时改小盲的上限(应 ≥ MIN_SMALL_BLIND,跨字段不强校,信运营)
    MIN_BUY_IN: int = Field(ge=1, le=1000000000)  # 运行时改房间默认买入的下限
    MAX_BUY_IN: int = Field(ge=1, le=1000000000)  # 运行时改房间默认买入的上限(应 ≥ MIN_BUY_IN)

    # ── 鉴权(auth.md;P5 国密安全信道)──
    PWD_HASH_ROUNDS: int = Field(ge=1, le=100000)  # 密码哈希 SM3 迭代轮数;注册/改密调 auth.passwords.hash_password 时传(拉伸抬高暴力成本)
    WS_FRAME_MAX_BYTES: int = Field(ge=256, le=1048576)  # 逐帧信道单帧字节上限;SecureChannel.open 拒超大帧(防解析放大)
    SESSION_TTL_SECONDS: int = Field(ge=60, le=86400)  # ws 会话 token 有效期(秒);SessionStore exp 兜底,客户端到期前无感轮换
    REST_FRAME_MAX_BYTES: int = Field(ge=256, le=1048576)  # REST 加密信封字节上限;open_envelope 拒超大信封(防解析放大,见 changes/0062)
    REST_REPLAY_WINDOW: int = Field(ge=1, le=4096)  # REST 防重放滑动窗宽度(容忍多深的并发/乱序请求;更旧一律拒,见 changes/0062)
    LOGIN_REPLAY_WINDOW_SECONDS: int = Field(ge=1, le=3600)  # 登录包新鲜窗 W(秒):|now - blob.ts| 超 W 拒;nonce 去重条目 TTL = 2W(须盖住 ts 超前时的整个新鲜期,见 changes/0063)
    KUSER_ROTATION_DAYS: int = Field(ge=1, le=90)  # K_user 轮换周期(天):issue/rotate 把 k_cur_until 排到 now+此值,轮换任务(scripts/kuser_admin.py rotate)挑到期者轮换
    KUSER_GRACE_DAYS: int = Field(ge=0, le=30)  # 旧钥宽限期(天):轮换后 k_prev 仍可登录这么久(附 rotate 提示),给还没手输新钥的人缓冲;0=立即失效

    # ── REST 查询(rest.md;changes/0050/0051）──
    LEADERBOARD_DEFAULT_LIMIT: int = Field(ge=1, le=1000)  # GET /leaderboard 不带 limit 时默认返回条数
    LEADERBOARD_MAX_LIMIT: int = Field(ge=1, le=1000)  # GET /leaderboard 的 limit 上限(防超大查询;应 ≥ DEFAULT)
    HANDS_DEFAULT_LIMIT: int = Field(ge=1, le=1000)  # GET /hands 不带 limit 时默认返回条数(一页手牌)
    HANDS_MAX_LIMIT: int = Field(ge=1, le=1000)  # GET /hands 的 limit 上限(应 ≥ DEFAULT)

    # ── dev 房(明文 dev 脚手架,见 changes/0018;非生产)──
    # 动态房(0049):无静态预置,shell 建 JoinRoom 时用下列默认配置建房(谁都可创建 / 空则消失)。
    DEV_ROOM: str  # dev 建议房名(客户端 join_room{room} 自选;非预置)
    DEV_SMALL_BLIND: int = Field(ge=1, le=100000)  # 新建房默认小盲(大盲 = 2×;建后任何在房成员可 SetSmallBlind 调)
    DEV_BUY_IN: int = Field(ge=1, le=100000000)  # 新建房默认买入额(Room.buy_in;实际买入额由 BuyIn 命令带)
    DEV_SEATS: int = Field(ge=2, le=10)  # 新建房座位数(Room.seats 长度)
    DEV_USERS: tuple[str, ...] = Field(min_length=1)  # 预置 dev 用户名(?nick= 取其一);env 写 JSON 数组
    DEV_START_POINTS: int = Field(ge=0, le=100000000)  # 每个 dev 用户的初始全局积分
    # dev 登录脚手架(changes/0060,dev-only 非生产):dev 用户共享口令 + 共享 K_user,使 /user/login 可真跑。
    # 生产走每用户带外 K_user + 各自密码(auth.md),此处明示放宽(dev 客户端要知 K_user 才能加密登录 blob)。
    DEV_PASSWORD: str = Field(min_length=1)  # 所有 dev 用户共享的登录口令
    DEV_KUSER: str = Field(pattern=r"^[0-9a-f]{32}$")  # 所有 dev 用户共享的 SM4 密钥(32 hex = 16B)


# 启动期单例:import 即建 → 缺字段/越界当场 ValidationError(config.md「缺了启动即报错」)。
config = GameConfig()


def __getattr__(name: str):
    # PEP 562 模块级 __getattr__:把 `gameconfig.ACTION_TIMEOUT` 透到单例字段,保持 0018 起的访问接口不变
    # (不必把 44 处调用点改成 config.XXX)。未知名 → getattr 自然抛 AttributeError。
    return getattr(config, name)
