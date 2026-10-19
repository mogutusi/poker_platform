"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

/**
 * 手牌历史搬进了「数据」页签(/stats):那里有最近手牌列表,点开看整手。
 * 这个地址保留成跳转,免得旧书签/旧入口落空。用 router.replace(客户端跳转):
 * 会话只在内存里,整页刷新会把人登出。
 */
export default function HistoryRedirect() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/stats")
  }, [router])
  return <div className="min-h-screen bg-background" />
}
