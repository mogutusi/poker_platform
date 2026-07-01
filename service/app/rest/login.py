# REST 登录端点(POST /user/login;P5 加密信道入口,见 docs/auth.md §登录握手 / changes/0057/0059)。
# 请求 {name, iv, blob=SM4(K_user,iv,{password,client_nonce})} → load_user_for_login → authenticate →
# SessionStore.create → 响应用 K_user 加密下发 {session_id, session_token, exp}(token 只此出现一次、被 K_user 护住)。
# fail-closed:任何一步不过一律 401「login failed」,不泄未知账号/密码错/blob 坏之别。无 JWT(身份从会话密钥解密得出)。
# 本端点是引导信道的入口,登录前无会话密钥,故它走 HTTP JSON、不套 0058 会话信封。

import json
import logging
import secrets
import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ttxsgm import sm4_cbc_enc

from app.auth.credentials import authenticate
from app.auth.session import SessionStore
from app.db.queries import load_user_for_login

log = logging.getLogger(__name__)

_RESP_IV_BYTES = 16  # 响应 SM4-CBC IV 长度(每次新鲜随机)


class LoginRequest(BaseModel):
    name: str  # 登录账号(明文,非秘密;服务器据此选 K_user)
    iv: str  # 请求 IV(16B,hex)
    blob: str  # SM4(K_user, iv, {password, client_nonce}) 的 hex


class LoginResponse(BaseModel):
    iv: str  # 响应 IV(hex)
    blob: str  # SM4(K_user, iv2, {session_id, session_token, exp}) 的 hex —— token 被 K_user 护住


def make_login_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker(setup() 前已建);session_store 为 shell 单例;now 可注入(测试确定 exp)。
    router = APIRouter()

    @router.post("/user/login", response_model=LoginResponse)
    async def login(req: LoginRequest) -> LoginResponse:
        # 任何一步不过一律 401「login failed」(fail-closed,不泄具体原因)。
        try:
            iv = bytes.fromhex(req.iv)
            blob = bytes.fromhex(req.blob)
        except ValueError:
            raise HTTPException(status_code=401, detail="login failed")
        try:
            user = await load_user_for_login(get_sessionmaker(), req.name)
        except Exception:  # 基础设施错(DB 连接/超时等)也归 401,不让原始异常冒成 500 泄故障 vs 认证之别
            log.exception("login: user lookup failed")  # 真因记日志供运维;查询无密码/密钥,不触脱敏红线
            raise HTTPException(status_code=401, detail="login failed")
        if user is None:
            raise HTTPException(status_code=401, detail="login failed")  # 未知账号 / name=NULL 老行
        proof = authenticate(user.hash_password, user.k_user, iv, blob)
        if proof is None:
            raise HTTPException(status_code=401, detail="login failed")  # 未启用 / 密码错 / blob 坏
        # client_nonce 重放守卫留待办(proof.client_nonce 已备用;登录重放低危,见 changes/0059)。
        session_id, session = session_store.create(user.name, user.nickname, now())
        # 响应用 K_user 加密:session_token 只在此加密下发一次、绝不明文上线(auth.md 铁律)。
        k_user = bytes.fromhex(user.k_user)  # authenticate 已验 k_user 为合法 16B hex,此处安全
        resp_iv = secrets.token_bytes(_RESP_IV_BYTES)
        payload = json.dumps(
            {"session_id": session_id, "session_token": session.token.hex(), "exp": session.expires_at}
        ).encode("utf-8")
        return LoginResponse(iv=resp_iv.hex(), blob=sm4_cbc_enc(k_user, resp_iv, payload).hex())

    return router
