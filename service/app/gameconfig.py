# 游戏可调参数(目标结构位,见 refactor/README §3)。
#
# 现阶段用带默认值的常量,import 不依赖 env(dev 友好);P8「配置收编」时接 poker.env + *.example
# (见 config.md / TODO)。与基础设施配置 app/config.py(DATABASE_URL/JWT…)分开:那是 .env、这是游戏参数。
# 旧 app/pokertable/gameconfig.py 是被取代的原型物(绑不存在的 poker.env),勿用。

# ── 定时器(见 timer.md)──
ACTION_TIMEOUT: float = 15.0  # 行动倒计时(秒):轮到某人后多久没动作即投 Timeout(默认 check / fold)
LIVENESS_TIMEOUT: float = 90.0  # 在线保活(秒):距最后一帧超此值即投 Cleanup(退筹释座);须 ≫ ACTION_TIMEOUT
TIMER_TICK_MS: int = 500  # Timer 扫描周期(毫秒):到点最多迟一个 tick

# ── 背压(见 architecture.md「队列有界」)──
INBOX_MAX: int = 1024  # GameLoop inbox 上限;满 = GameLoop 卡死(进程级 bug,落 CRITICAL)
OUTBOUND_MAX: int = 256  # 每连接 outbound 上限;满 = 慢客户端,丢连接 + Disconnect(不阻塞 GameLoop)
ERROR_DETAIL_MAX_LEN: int = 200  # 回发 ErrorMessage.detail 的最大字符数(截断 Pydantic 校验错误文本)

# ── 房聊文本防护 / 限速(见 messaging.md;shell 进 reduce 前防护;P8 接 poker.env + bounds)──
ROOM_CHAT_MAX_TEXT_LEN: int = 500  # 房聊单条正文最大字符数;超则 Receiver 拒(MESSAGE_TOO_LONG)
ROOM_CHAT_RATE_BURST: float = 5.0  # 令牌桶容量(突发上限):静默后最多连发几条
ROOM_CHAT_RATE_PER_SEC: float = 1.0  # 令牌桶稳态补充速率(每秒令牌数 = 每秒可发条数)
ROOM_CHAT_HISTORY_SIZE: int = 50  # 每房内存环形缓冲保留的最近房聊条数(不落库;进/重进房经 FetchRoomChat 拉,见 messaging.md)

# ── 私信 DM 文本防护 / 限速(见 messaging.md §私信;shell 进路由前防护;P8 接 poker.env + bounds)──
DM_MAX_TEXT_LEN: int = 1000  # 私信单条正文最大字符数;超则 shell DM 路由拒(MESSAGE_TOO_LONG)
DM_RATE_BURST: float = 5.0  # 私信令牌桶容量(突发上限):静默后最多连发几条
DM_RATE_PER_SEC: float = 1.0  # 私信令牌桶稳态补充速率(每秒可发条数)
# 注:已读保留清理参数(DM_READ_RETENTION_SECONDS / DM_CLEANUP_INTERVAL_SECONDS)随 0039 已读游标落地(见 db.md)

# ── 日志(见 log.md;P8 接 poker.env + bounds)──
LOG_LEVEL: str = "INFO"  # root 级别:DEBUG=全量审计(开发/排障)/ INFO=业务里程碑 / WARNING+=异常路径
LOG_FORMAT: str = "console"  # "json"=结构化一行一条(生产,jq 过滤)/ "console"=人类友好单行(本地)
LOG_FILE: str = ""  # 落地文件路径;空串=只写 stderr(dev 默认)

# ── delayDB 写回(见 db.md;P8 接 poker.env + bounds)──
DB_FLUSH_INTERVAL_MS: int = 500  # PersistWriter 落库周期(毫秒)= 同实体多次变更合并窗 = 积分落库最大滞后 / 崩溃窗口
DB_WRITE_MAX_RETRY: int = 10  # 同批连续落库失败**达**此次数(总尝试数,非额外重试)→ 毒丸:丢批 + CRITICAL(别卡死后续,bug 信号)
DB_DRAIN_TIMEOUT_MS: int = 5000  # 优雅关闭 drain 上限(毫秒):超时放弃未落写 + CRITICAL(进程要退,接受该窗口)

# ── dev 房(明文 dev 脚手架,见 changes/0018;非生产)──
DEV_ROOM: str = "dev"  # 明文 dev 端点预置的单一房名
DEV_SMALL_BLIND: int = 1  # dev 房小盲(大盲 = 2×)
DEV_BUY_IN: int = 100  # dev 房默认买入额(Room.buy_in;实际买入额由 BuyIn 命令带)
DEV_SEATS: int = 6  # dev 房座位数
DEV_USERS: tuple[str, ...] = ("alice", "bob", "carol", "dave", "eve", "frank")  # 预置 dev 用户名(?nick= 取其一)
DEV_START_POINTS: int = 1000  # 每个 dev 用户的初始全局积分
