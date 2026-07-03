# 用户资料 REST(rest.md §用户资料;P5 加密信封的首个消费者,见 changes/0062)。
# POST /user/me:信封拆包(open_request:查会话→REST 域密钥→MAC→解密→防重放窗)→ 身份 = 会话 name →
# 读 DB 资料投影 → 信封封回(seal_response,seq 回显绑定)。改昵称 / 改密码随后续砖(需 Presence/rename 联动)。
# 错误两段式:信封不过 = 统一 401(secure.py);信封验过后的 DB 错/行缺失 = 明文 500 无细节(已认证,非鉴权问题)。

import logging
import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.session import SessionStore
from app.db.queries import load_profile_by_name
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

    return router
