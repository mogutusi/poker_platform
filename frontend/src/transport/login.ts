// 登录握手(见 docs/transport.md §一 / service/docs/auth.md §登录握手)。
// 用 K_user 加密一来一回,换回会话凭证;token 只在这一次被 K_user 护着下发,之后绝不上线。

import { bytesToHex, bytesToUtf8, hexToBytes, randomIv, sm4CbcDecrypt, sm4CbcEncrypt, utf8ToBytes } from '@/crypto'
import { API_BASE_URL } from './config'
import { loadKUser, startSession, type Session } from './session'

interface LoginResponseBody {
  iv: string
  blob: string
}

interface LoginPayload {
  session_id: string
  session_token: string
  exp: number
  rotate?: boolean
}

export class LoginError extends Error {
  constructor(
    message: string,
    /** 'no_key' 缺 K_user;'rejected' 服务器 401;'bad_response' 响应解不开;'network' 连不上 */
    readonly kind: 'no_key' | 'rejected' | 'bad_response' | 'network',
  ) {
    super(message)
  }
}

/**
 * 登录。成功后会话已就绪(密钥派生好、seq 归零),调用方接着连 ws 即可。
 *
 * 服务器对「账号不存在 / 密码错 / blob 坏」一律回 401 且不区分,所以这里也只能给一个笼统的失败提示。
 */
export async function login(name: string, password: string): Promise<Session> {
  const kUser = loadKUser()
  if (!kUser) throw new LoginError('尚未设置 K_user', 'no_key')

  const iv = randomIv()
  // 每次登录都要新的 nonce 和当前 ts:服务器有 freshness + nonce 去重两道重放守卫,
  // 复用上次的请求体重发必被拒。
  const payload = {
    password,
    client_nonce: bytesToHex(randomIv()),
    ts: Math.floor(Date.now() / 1000),
  }
  const blob = sm4CbcEncrypt(kUser, iv, utf8ToBytes(JSON.stringify(payload)))

  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}/user/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, iv: bytesToHex(iv), blob: bytesToHex(blob) }),
    })
  } catch {
    throw new LoginError('连不上服务器', 'network')
  }

  if (!res.ok) throw new LoginError('账号、密码或密钥不对', 'rejected')

  let decoded: LoginPayload
  try {
    const body = (await res.json()) as LoginResponseBody
    const plain = sm4CbcDecrypt(kUser, hexToBytes(body.iv), hexToBytes(body.blob))
    decoded = JSON.parse(bytesToUtf8(plain)) as LoginPayload
  } catch {
    // 解不开通常意味着本地 K_user 和服务器那把不是同一把。
    throw new LoginError('响应无法解密,请检查 K_user 是否为最新', 'bad_response')
  }

  return startSession({
    sessionId: decoded.session_id,
    sessionToken: hexToBytes(decoded.session_token),
    expiresAt: decoded.exp,
    rotateHint: decoded.rotate === true,
  })
}
