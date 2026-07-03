# REST 加密信封助手(P5,见 docs/auth.md §加密信道「REST」/ changes/0062)。需身份的 REST 端点共用:
# 请求 POST body {sid, frame=hex(iv‖ct‖mac)} → 查会话 → REST 域密钥(derive_rest_keys,与 ws 分域)→
# open_envelope(结构→MAC→解密→取 seq)→ 每会话滑动窗防重放 → 内层 JSON = 端点参数;身份 = 会话(解密即认证)。
# 响应 {frame},seq **回显请求 seq**(请求-响应绑定:重放旧响应答新请求必被客户端 seq 校验拒)。
# fail-closed:信封任何一步不过 → 统一 401(不泄败因,同 /user/login);信封验过后的失败由端点自定(500 等)。
# 脱敏红线(log.md):token/密钥/明文/密文不进日志,失败只记分类 reason。

import json
import logging

from fastapi import HTTPException
from pydantic import BaseModel

from app import gameconfig
from app.auth.channel import FrameError, ReplayWindow, derive_rest_keys, open_envelope, seal_envelope
from app.auth.session import Session, SessionStore

log = logging.getLogger(__name__)


class SecureRequest(BaseModel):
    sid: str  # 公开 selector(= session_id,登录下发);服务器据此查会话取密钥 + 身份
    frame: str  # hex(iv‖ct‖mac);内层明文 = seq(8B) ‖ 端点参数 JSON(REST 域密钥封,与 ws 帧分域)


class SecureResponse(BaseModel):
    frame: str  # hex(iv‖ct‖mac);内层明文 = 请求 seq(回显,绑定)‖ 响应 JSON


def _reject(reason: str) -> HTTPException:
    # 统一 401(fail-closed):不泄「sid 不识 / 过期 / MAC 坏 / 重放」之别;真因只落日志分类供定位。
    log.warning("secure request rejected reason=%s", reason)
    return HTTPException(status_code=401, detail="unauthorized")


def open_request(session_store: SessionStore, req: SecureRequest, now: float) -> tuple[Session, int, dict]:
    # 拆一个 REST 加密请求:返回 (会话, 请求 seq, 参数 dict)。任何一步不过 raise 统一 401。
    session = session_store.lookup(req.sid, now)
    if session is None:
        raise _reject("unknown_or_expired_sid")
    try:
        frame = bytes.fromhex(req.frame)
    except ValueError:
        raise _reject("bad_frame_hex")
    enc_key, mac_key = derive_rest_keys(session.token)
    try:
        seq, plaintext = open_envelope(enc_key, mac_key, frame, gameconfig.REST_FRAME_MAX_BYTES)
    except FrameError as e:
        raise _reject(e.reason)
    if session.rest_window is None:  # 首个 REST 请求 lazy 建窗(同 ws 信道的 get-or-derive)
        session.rest_window = ReplayWindow(gameconfig.REST_REPLAY_WINDOW)
    if not session.rest_window.accept(seq):  # MAC 已验(真包)但 seq 重放/太旧 → 拒
        raise _reject("replayed_seq")
    try:
        payload = json.loads(plaintext)
    except ValueError:
        raise _reject("bad_payload_json")
    if not isinstance(payload, dict):  # 端点参数一律对象形(空参 = {}),其余形状拒
        raise _reject("bad_payload_shape")
    return session, seq, payload


def seal_response(session: Session, seq: int, payload: dict) -> SecureResponse:
    # 封响应:REST 域密钥 + 回显请求 seq(客户端验 MAC → 解密 → seq == 我发的,绑定请求-响应)。
    enc_key, mac_key = derive_rest_keys(session.token)
    frame = seal_envelope(enc_key, mac_key, seq, json.dumps(payload).encode("utf-8"))
    return SecureResponse(frame=frame.hex())
