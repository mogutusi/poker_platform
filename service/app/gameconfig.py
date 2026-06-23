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

# ── dev 房(明文 dev 脚手架,见 changes/0018;非生产)──
DEV_ROOM: str = "dev"  # 明文 dev 端点预置的单一房名
DEV_SMALL_BLIND: int = 1  # dev 房小盲(大盲 = 2×)
DEV_BUY_IN: int = 100  # dev 房默认买入额(Room.buy_in;实际买入额由 BuyIn 命令带)
DEV_SEATS: int = 6  # dev 房座位数
DEV_USERS: tuple[str, ...] = ("alice", "bob", "carol", "dave", "eve", "frank")  # 预置 dev 用户名(?nick= 取其一)
DEV_START_POINTS: int = 1000  # 每个 dev 用户的初始全局积分
