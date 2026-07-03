# 用户资料 REST(rest.md §用户资料;P5 加密信封消费者,见 changes/0062 /user/me、0064 /user/password)。
# 信封拆包(open_request:查会话→REST 域密钥→MAC→解密→防重放窗)→ 身份 = 会话 name → 读/写 DB → 信封封回。
# 错误分层:信封不过=401(secure.py 统一);业务失败(旧密码错=403 / 请求畸形=400);基础设施(DB 错/行缺失)=500。
# 改昵称随后续砖(需 Presence/rename/多会话联动)。

import logging
import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.passwords import hash_password, verify_password
from app.auth.session import SessionStore
from app.db.queries import load_password_for_change, load_profile_by_name
from app.db.user_writes import update_password_hash
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response

log = logging.getLogger(__name__)


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
