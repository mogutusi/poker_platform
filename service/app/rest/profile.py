# 用户资料 REST(rest.md §用户资料;P5 加密信封消费者,见 changes/0062 /user/me、0064 /user/password、0065 /user/nickname)。
# 信封拆包(open_request:查会话→REST 域密钥→MAC→解密→防重放窗)→ 身份 = 会话 name → 读/写 DB → 信封封回。
# 错误分层:信封不过=401(secure.py 统一);业务失败(旧密码错·在房=403 / 撞名=409 / 请求畸形=400);基础设施=500。
# 改昵称是独立工厂 make_nickname_router(需 Presence/conns,依赖面不同,免动 make_profile_router 既有签名)。

import logging
import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.passwords import hash_password, verify_password
from app.auth.session import SessionStore
from app.db.queries import load_identity_by_name, load_password_for_change, load_profile_by_name, nickname_taken
from app.db.user_writes import update_nickname, update_password_hash
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response
from app.shell.connection import ConnectionManager
from app.shell.presence import Presence

log = logging.getLogger(__name__)

_NICKNAME_MAX_LEN = 50  # 对齐 db/models.py User.nickname 的 max_length(schema 常量,非可调参数)


def make_profile_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker(同 make_login_router);session_store 为 shell 单例;now 可注入(测试确定过期)。
    router = APIRouter()

    @router.post("/user/me", response_model=SecureResponse)
    async def user_me(req: SecureRequest) -> SecureResponse:
        session, seq, _params = open_request(session_store, req, now())  # 信封不过 → 统一 401;/user/me 无参({})
        try:
            profile = await load_profile_by_name(get_sessionmaker(), session.name)
        except Exception:  # DB 错:已认证,非鉴权问题 → 如实 500(无敏感 body);真因落日志供运维
            log.exception("user_me: profile lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if profile is None:  # 会话在、DB 行没了 = 内部不一致(种子/迁移事故),同 500
            log.error("user_me: session name has no DB row")  # 不记 name 之外的敏感字段(本就无)
            raise HTTPException(status_code=500, detail="internal")
        name, nickname, points = profile
        return seal_response(session, seq, {"name": name, "nickname": nickname, "points": points})

    @router.post("/user/password", response_model=SecureResponse)
    async def change_password(req: SecureRequest) -> SecureResponse:
        # 改密码(信封内参 {old_password, new_password}):验旧密码(第二因子,防盗 token 锁死)→ 重算哈希 → 同步直写。
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        old_password = params.get("old_password")
        new_password = params.get("new_password")
        if not isinstance(old_password, str) or not isinstance(new_password, str):
            raise HTTPException(status_code=400, detail="bad request")  # 缺参 / 非字符串:请求畸形
        if not new_password.strip():
            raise HTTPException(status_code=400, detail="empty new password")  # 新密码非空底线(复杂度策略属未来)
        try:
            row = await load_password_for_change(get_sessionmaker(), session.name)
        except Exception:  # 基础设施错(已认证,非鉴权问题)→ 500;真因落日志(查询无密码明文)
            log.exception("change_password: lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if row is None:  # 会话在、DB 行没了 = 内部不一致
            log.error("change_password: session name has no DB row")
            raise HTTPException(status_code=500, detail="internal")
        uid, current_hash = row
        if current_hash is None or not verify_password(old_password, current_hash):
            # 旧密码不可验(未启用)或错:已认证但此操作不允许 → 403,不改库(密码/摘要不进日志)
            raise HTTPException(status_code=403, detail="password change denied")
        new_hash = hash_password(new_password, gameconfig.PWD_HASH_ROUNDS)  # 新盐重算(改成同密码也换哈希)
        try:
            await update_password_hash(get_sessionmaker(), uid, new_hash)  # 同步直写(鉴权列,delayDB 之外,见 db.md)
        except Exception:
            log.exception("change_password: write failed")
            raise HTTPException(status_code=500, detail="internal")
        return seal_response(session, seq, {"status": "ok"})

    return router


def make_nickname_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    get_presence: Callable[[], "Presence | None"],
    conns: ConnectionManager,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 改昵称(rest.md/presence.md,changes/0065)。独立工厂:依赖 Presence(setup() 后才建 → 迟绑 getter)
    # 与 ConnectionManager,与 make_profile_router 依赖面不同,分开免动既有签名/测试。
    router = APIRouter()

    @router.post("/user/nickname", response_model=SecureResponse)
    async def change_nickname(req: SecureRequest) -> SecureResponse:
        # 仅大厅可改(nickname 是 world 的键,在用时改会键错乱)→ DB 直写 → 会话表 + 连接键联动(三处,顺序见 0065)。
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        new_nick = params.get("new_nickname")
        if (
            not isinstance(new_nick, str)
            or not new_nick
            or new_nick != new_nick.strip()  # 首尾空白拒:" Bob" 与 "Bob" 视觉同名、键不同 = 冒充面(0065 自 review)
            or len(new_nick) > _NICKNAME_MAX_LEN
        ):
            raise HTTPException(status_code=400, detail="bad nickname")  # 非串 / 空 / 带首尾空白 / 超长:请求畸形
        presence = get_presence()
        if presence is None:  # 未接线(启动序错):基础设施问题
            log.error("change_nickname: presence not wired")
            raise HTTPException(status_code=500, detail="internal")
        try:
            row = await load_identity_by_name(get_sessionmaker(), session.name)
        except Exception:
            log.exception("change_nickname: lookup failed")
            raise HTTPException(status_code=500, detail="internal")
        if row is None:  # 会话在、DB 行没了 = 内部不一致
            log.error("change_nickname: session name has no DB row")
            raise HTTPException(status_code=500, detail="internal")
        uid, old_nick = row  # 昵称以 DB 为准(会话表可能滞后)
        live_conn = conns.get(old_nick)  # 此刻捕获本人 live 连接(await 窗后键可能已被并发改名动过,rekey 按对象不按键)
        if new_nick == old_nick:
            raise HTTPException(status_code=400, detail="nickname unchanged")  # 同名无意义,拒(rename 语义干净)
        if presence.current_room(old_nick) is not None:
            # 在房不许改(world 键错乱);presence 读 committed world、可滞后一拍(presence.md 记档接受)
            raise HTTPException(status_code=403, detail="cannot change nickname in room")
        try:
            if await nickname_taken(get_sessionmaker(), new_nick):
                raise HTTPException(status_code=409, detail="nickname taken")  # 预查:干净拒
            # CAS:WHERE uid AND nickname=old_nick——同账号并发双改名只有一个赢(0 命中 = 输者 → 409,不做内存联动)
            if not await update_nickname(get_sessionmaker(), uid, old_nick, new_nick):
                raise HTTPException(status_code=409, detail="nickname changed concurrently")
        except IntegrityError:
            # 预查与写之间有 await 窗,并发撞名由唯一约束兜底 → 同 409(见 0065 决策 3)
            raise HTTPException(status_code=409, detail="nickname taken")
        except HTTPException:
            raise
        except Exception:
            log.exception("change_nickname: write failed")
            raise HTTPException(status_code=500, detail="internal")
        # DB 已落定(CAS 赢家)→ 内存联动(纯同步、其间无 await ⇒ 原子):该账号全部会话 + 捕获的那个连接对象
        # (rekey 按对象 `is` 判定,不按键 pop——防 await 窗内键已被他人 rename 占走时误挂他人连接,0065 自 review)。
        session_store.rename_nickname(session.name, new_nick)
        if live_conn is not None:
            conns.rekey(live_conn, new_nick)
        return seal_response(session, seq, {"status": "ok", "nickname": new_nick})

    return router
