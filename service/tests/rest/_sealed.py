"""REST 加密信封的测试脚手架(0094 抽出)。

信封收编三个读端点之后,「封请求 / 拆响应」这套 10 行在 6 个测试文件里各抄一份就太多了——
而它直接压在协议面上(密钥分域、seq 回显、内层 JSON 形状),抄一份就多一处会漏改的地方。
测试不装 httpx/TestClient,一律取 `APIRoute.endpoint` 直接 await(见各文件的 `_endpoint`)。
"""

import json

from app.auth.channel import derive_rest_keys, open_envelope, seal_envelope
from app.auth.session import Session
from app.rest.secure import SecureRequest, SecureResponse

T0 = 1_000_000.0  # 固定「现在」;会话过期与否由测试自己控
TTL = 3600
MAX_FRAME = 65536


def seal_req(sid: str, session: Session, seq: int, params: dict) -> SecureRequest:
    # 客户端侧:用 **REST 域**密钥(与 ws 分域)把参数封成 {sid, frame}。
    # sid 单独传:`Session` 里没有它——公开句柄由 `SessionStore.create` 另行返回,秘密与句柄分开放。
    enc, mac = derive_rest_keys(session.token)
    frame = seal_envelope(enc, mac, seq, json.dumps(params).encode())
    return SecureRequest(sid=sid, frame=frame.hex())


def open_resp(session: Session, resp: SecureResponse) -> tuple[int, dict]:
    # 客户端侧:拆响应,返回 (回显的 seq, 内层 JSON)。seq 必须等于请求 seq(请求-响应绑定)。
    enc, mac = derive_rest_keys(session.token)
    seq, plaintext = open_envelope(enc, mac, bytes.fromhex(resp.frame), MAX_FRAME)
    return seq, json.loads(plaintext)


async def call(endpoint, sid: str, session: Session, params: dict, seq: int = 1) -> dict:
    # 一来一回:封参数 → await 端点 → 拆响应并核对 seq 回显。返回内层 JSON。
    resp = await endpoint(seal_req(sid, session, seq, params))
    echoed, payload = open_resp(session, resp)
    assert echoed == seq, "响应必须回显请求 seq(绑定请求-响应)"
    return payload
