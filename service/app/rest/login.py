# REST 登录端点(POST /user/login;P5 加密信道入口,见 docs/auth.md §登录握手 / changes/0057/0059)。
# 请求 {name, iv, blob=SM4(K_user,iv,{password,client_nonce,ts})} → load_user_for_login → authenticate →
# SessionStore.create → 响应用 K_user 加密下发 {session_id, session_token, exp, rotate}(token 只此出现一次、被 K_user 护住)。
# fail-closed:任何一步不过一律 401「login failed」,不泄未知账号/密码错/blob 坏/重放之别。无 JWT(身份从会话密钥解密得出)。
# 重放守卫(0063):blob 内带 ts + client_nonce —— freshness 窗(LOGIN_REPLAY_WINDOW_SECONDS)+ 窗口内 nonce 去重。
# 双钥轮换(0066):不带 key_version,服务器先试 k_cur、败再试宽限内 k_prev(用户手输密钥无从知版本);
# k_prev 命中 → 响应 rotate=true 提示尽快换新钥;响应必须用**匹配到的那把**加密(旧钥客户端解不开新钥密文)。
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

from app import gameconfig
from app.auth.credentials import authenticate
from app.auth.nonce import NonceCache
from app.auth.session import SessionStore
from app.db.queries import load_user_for_login

log = logging.getLogger(__name__)

_RESP_IV_BYTES = 16  # 响应 SM4-CBC IV 长度(每次新鲜随机)


class LoginRequest(BaseModel):
    name: str  # 登录账号(明文,非秘密;服务器据此选 K_user)
    iv: str  # 请求 IV(16B,hex)
    blob: str  # SM4(K_user, iv, {password, client_nonce, ts}) 的 hex(ts=客户端 epoch 秒,0063 起必填)


class LoginResponse(BaseModel):
    iv: str  # 响应 IV(hex)
    blob: str  # SM4(匹配到的 K_user, iv2, {session_id, session_token, exp, rotate}) 的 hex —— token 被 K_user 护住;rotate=true 提示客户端在用旧钥、尽快换新(0066)


def make_login_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession]],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker(setup() 前已建);session_store 为 shell 单例;now 可注入(测试确定 exp)。
    # nonce 去重缓存活在本 router(单 create_app 单实例;重启清空 → freshness 窗内旧包可复活一次,记档接受,0063)。
    nonce_cache = NonceCache()
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
        # 双钥两次尝试(0066 决策 1):先 k_cur;败且 k_prev 仍在宽限内再试 k_prev(命中即知客户端在用旧钥
        # → rotate 提示)。错钥路径在 SM4 解密/JSON 解析即败、不进昂贵的 verify_password,两次尝试代价可忽略。
        # k_prev_until 为 NULL 视同过期(fail-closed:轮换总是成对盖 prev+until,缺 until 的脏行不放行)。
        issued_at = now()
        matched_key, rotate = user.k_cur, False
        proof = authenticate(user.hash_password, user.k_cur, iv, blob)
        if proof is None and user.k_prev is not None and user.k_prev_until is not None and issued_at <= user.k_prev_until:
            proof = authenticate(user.hash_password, user.k_prev, iv, blob)
            if proof is not None:
                matched_key, rotate = user.k_prev, True
        if proof is None:
            raise HTTPException(status_code=401, detail="login failed")  # 未启用 / 密码错 / blob 坏 / 缺 ts / 旧钥过宽限
        # 重放守卫(0063,凭证验过才查——伪造包灌不进 nonce 缓存):① freshness:|now-ts| 超窗即旧包/坏钟;
        # ② 窗口内 (name, nonce) 去重。两者相与:重放包要么 ts 过期、要么 nonce 撞库。失败仍统一 401(不泄败因)。
        if abs(issued_at - proof.ts) > gameconfig.LOGIN_REPLAY_WINDOW_SECONDS:
            log.warning("login rejected reason=stale_ts")  # 只记分类;不记 ts/nonce 之外的凭证内容(本就无)
            raise HTTPException(status_code=401, detail="login failed")
        # nonce 条目 TTL = 2×新鲜窗:blob 的 ts 可超前 now 至 W(freshness 容偏斜),其新鲜期最晚到 ts+W ≤ now+2W
        # ——条目必须盖住 blob 整个可通过 freshness 的时段,否则「条目先过期、blob 还新鲜」出现重放缝(changes/0063)。
        if not nonce_cache.check_and_add(
            user.name, proof.client_nonce, issued_at, 2 * gameconfig.LOGIN_REPLAY_WINDOW_SECONDS
        ):
            log.warning("login rejected reason=replayed_nonce")
            raise HTTPException(status_code=401, detail="login failed")
        session_id, session = session_store.create(user.name, user.nickname, issued_at)
        # 响应用**匹配到的** K_user 加密(0066:旧钥登录须旧钥封响应,否则客户端解不开):
        # session_token 只在此加密下发一次、绝不明文上线(auth.md 铁律);rotate=true = 在用旧钥、尽快换新。
        k_user = bytes.fromhex(matched_key)  # authenticate 已验匹配键为合法 16B hex,此处安全
        resp_iv = secrets.token_bytes(_RESP_IV_BYTES)
        payload = json.dumps(
            {
                "session_id": session_id,
                "session_token": session.token.hex(),
                "exp": session.expires_at,
                "rotate": rotate,
            }
        ).encode("utf-8")
        return LoginResponse(iv=resp_iv.hex(), blob=sm4_cbc_enc(k_user, resp_iv, payload).hex())

    return router
