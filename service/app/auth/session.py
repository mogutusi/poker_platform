# ws 会话表(P5 鉴权,见 docs/auth.md §登录握手 / §密钥层级)。/user/login 成功后铸
# session_id(公开句柄)+ session_token(32B 秘密票据)+ exp,登记 session_id → Session;ws 握手 ?sid=
# 查表拿 token(派生逐帧密钥,见 auth/channel.py)+ nickname(投 Connect)。内存 shell 状态(同原型
# _refresh_token_pool),进程重启即失效 → 重登,可接受。鉴权秘密只在 shell,绝不进 world(user.md)。
# 时钟外移:create/lookup/prune 收显式 now(epoch 秒),无隐藏时钟依赖、过期逻辑可测(同 timer.md)。
# 脱敏红线(log.md):session_token 任何级别不进日志。

import secrets
from dataclasses import dataclass, field

from app.auth.channel import ReplayWindow, SecureChannel

_SESSION_ID_BYTES = 16  # session_id 随机字节数(token_urlsafe 前);公开句柄,进 ws?sid= 明文无妨
_SESSION_TOKEN_BYTES = 32  # session_token 秘密字节数;派生逐帧 enc/mac 密钥,永不再上线


@dataclass
class Session:
    # 一条 ws 会话(内存 shell 状态,不进 world)。
    name: str  # 登录账号(不可变;登录定位用户/选 K_user)
    nickname: str  # 游戏昵称(握手后投 Connect(nick) 接入大厅)
    token: bytes = field(repr=False)  # 32B 会话票据(= session_token;秘密,派生逐帧密钥)。repr=False:脱敏红线,防误 print/log 泄露(log.md)
    expires_at: float  # 过期墙钟(epoch 秒);now >= 此值即失效(服务器 exp 兜底)。`0.0` 是**吊销墓碑**:revoke 就地写入,让持有本对象的活连接下一帧即被判过期关掉(见 SessionStore.revoke)
    channel: SecureChannel | None = field(default=None, repr=False)  # 本会话逐帧信道(ws 首次握手 get-or-derive 缓存;跨重连复用 → seq 逐会话连续,见 changes/0061)。repr=False:含密钥,脱敏红线
    rest_window: ReplayWindow | None = field(default=None, repr=False)  # 本会话 REST 防重放滑动窗(首个 REST 请求 lazy 建;与 ws seq 分域独立,见 changes/0062)


class SessionStore:
    # ws 会话表:session_id(公开句柄)→ Session。shell 单例(将挂 lifespan,同 ConnectionManager)。
    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds  # 会话有效期(秒,gameconfig.SESSION_TTL_SECONDS);create 时 exp=now+ttl
        self._by_id: dict[str, Session] = {}

    def create(self, name: str, nickname: str, now: float) -> tuple[str, Session]:
        # 铸新会话:公开 session_id + 秘密 32B token,登记带 exp=now+ttl。返回 (session_id, Session)。
        # id/token 独立随机;不强制每用户单会话(轮换靠新登录 + 连接层顶替,会话层可并存新旧)。
        # 先扫过期(0070):被静默轮换抛弃的旧会话永远不会再被 lookup,惰性删够不着 → 每次登录
        # 主动清一遍(清扫频率 = 登录频率,零额外接线;过期密钥不常驻内存)。
        self.prune(now)
        session_id = secrets.token_urlsafe(_SESSION_ID_BYTES)
        token = secrets.token_bytes(_SESSION_TOKEN_BYTES)
        session = Session(name=name, nickname=nickname, token=token, expires_at=now + self._ttl)
        self._by_id[session_id] = session
        return session_id, session

    def lookup(self, session_id: str, now: float) -> Session | None:
        # 查会话;过期(now >= exp)当无效并顺手删(惰性清),返回 None。未知 id 也返回 None。
        session = self._by_id.get(session_id)
        if session is None:
            return None
        if now >= session.expires_at:
            del self._by_id[session_id]
            return None
        return session

    def revoke(self, session_id: str) -> bool:
        # 吊销单个会话(登出 / 改密后清其它设备);未知 id 无害幂等,返回是否真的吊销了一条。
        # **摘表项还不够**:活 ws 连接持有的是 Session 对象与从它派生的 SecureChannel,每帧只比对
        # conn.session.expires_at(receiver 收帧 / sender 出站),从不回头查表——只 pop 的话,已经连着的人
        # 照样收发,而吊销要防的恰恰是「凭证已泄露、对方可能已连着」。所以就地把对象判死,复用 0070 那条
        # 既有强制路径:下一帧(任一方向)即关连接 4401。零流量的连接活到下次有活动,同 auth.md 记的例外。
        session = self._by_id.pop(session_id, None)
        if session is None:
            return False
        session.expires_at = 0.0
        return True

    def revoke_all_for_name(self, name: str, *, except_id: str | None = None) -> int:
        # 吊销该登录账号的全部会话(改密自救:旧会话不清,改密就防不住已泄露的 token),返回吊销条数。
        # except_id 保留当前这一个,免得改密的人自己被踢下线。
        # 线性扫而不建 name→ids 索引:同类的 rename_nickname 已是同款扫法,规模锁死在线 ≤20;
        # 索引是第二份事实源,create/lookup 惰性删/prune/revoke 四处都要维护,漂一处就是「吊销不干净」。
        targets = [sid for sid, session in self._by_id.items() if session.name == name and sid != except_id]
        for sid in targets:
            self.revoke(sid)
        return len(targets)

    def rename_nickname(self, name: str, new_nick: str) -> int:
        # 改昵称联动(changes/0065):该登录账号 name 的**全部**会话(含其它设备)nickname 改为 new_nick,
        # 返回改动条数。否则旧会话再握手 ws 会以旧 nick 接入。纯内存同步操作,与 DB 写的顺序由调用方保证。
        changed = 0
        for session in self._by_id.values():
            if session.name == name:
                session.nickname = new_nick
                changed += 1
        return changed

    def prune(self, now: float) -> int:
        # 周期主动清过期会话(避免过期行长滞留;惰性清只在 lookup 命中时触发)。返回清理条数。
        expired = [sid for sid, session in self._by_id.items() if now >= session.expires_at]
        for sid in expired:
            del self._by_id[sid]
        return len(expired)

    def __len__(self) -> int:
        # 当前登记(含尚未清的过期)会话数;供测试/监控。
        return len(self._by_id)
