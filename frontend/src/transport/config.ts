// 后端地址。默认 dev 的 localhost:8000,可用 NEXT_PUBLIC_API_URL 覆盖(见 docs/dev.md)。

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/** 把 http(s) 基址换成 ws(s),ws 端点与 REST 同源。 */
export function wsUrl(sessionId: string): string {
  const base = API_BASE_URL.replace(/^http/, 'ws')
  return `${base}/ws?sid=${encodeURIComponent(sessionId)}`
}
