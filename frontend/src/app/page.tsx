"use client"

import type React from "react"
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { login, LoginError } from "@/transport/login"
import { hasKUser, saveKUser } from "@/transport/session"
import pokerRoomBg from "@/pics/poker-room.png"

export default function PokerLoginPage() {
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  // K_user 是带外发放的每用户共享密钥,每周轮换。没有它就无法构造登录 blob,所以必须能填。
  // 已缓存过就不再要求重填,只留一个「换一把钥匙」的入口。
  const [kUser, setKUser] = useState("")
  const [needKUser, setNeedKUser] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    setNeedKUser(!hasKUser())
    // 被服务器断开时,是带着原因跳回这一页的(见 transport/ws.ts)。不说清楚的话,用户看到的
    // 只是「莫名其妙回到了登录页」。用 location 读而不是 useSearchParams:后者会逼整棵子树进
    // Suspense 边界(Next 15 的要求,0077 在 /game 上踩过)。
    const reason = new URLSearchParams(window.location.search).get("reason")
    if (reason === "displaced") setNotice("这个账号刚在别处登录,本页的连接已被接管。")
    else if (reason === "expired") setNotice("会话已过期,请重新登录。")
  }, [])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setNotice(null)
    setIsLoading(true)

    try {
      if (needKUser || kUser.trim()) {
        saveKUser(kUser)
      }

      const session = await login(username, password)
      setNeedKUser(false)
      if (session.rotateHint) {
        // 服务器是用旧钥认出你的,宽限期过后这把就失效了。
        setNotice("你在用旧的 K_user，请尽快向管理员换新钥匙。")
      }
      router.push("/lobby")
    } catch (err) {
      if (err instanceof LoginError) {
        setError(
          err.kind === "no_key"
            ? "请先填入管理员发给你的 K_user。"
            : err.message,
        )
        if (err.kind === "no_key" || err.kind === "bad_response") setNeedKUser(true)
      } else if (err instanceof Error) {
        setError(err.message)
      } else {
        setError("登录失败")
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
      <div 
        className="min-h-screen relative overflow-hidden flex items-center justify-center p-4"
        style={{
          backgroundImage: `url(${pokerRoomBg.src})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
          backgroundRepeat: "no-repeat",
          backgroundAttachment: "fixed",
        }}
      >
        {/* Subtle vignette overlay */}
        <div className="absolute inset-0 bg-gradient-radial from-transparent via-black/20 to-black/40 z-0"></div>

        {/* Gaming-style login form */}
        <div className="w-full max-w-md relative z-10 mt-40">
          <form onSubmit={handleLogin} className="space-y-6">
            {error && (
              <div 
                className="p-4 text-sm bg-red-900/80 backdrop-blur-md border-2 border-red-500/50 rounded-lg text-white font-semibold"
                style={{
                  textShadow: "0 0 10px rgba(239, 68, 68, 0.8), 0 0 20px rgba(239, 68, 68, 0.4)",
                  boxShadow: "0 0 20px rgba(239, 68, 68, 0.3), inset 0 0 20px rgba(239, 68, 68, 0.1)"
                }}
              >
                {error}
              </div>
            )}
            <div className="space-y-3">
              <Label 
                htmlFor="username" 
                className="text-white font-bold text-lg tracking-wider uppercase"
                style={{
                  textShadow: "0 0 10px rgba(212, 175, 55, 0.8), 0 2px 4px rgba(0, 0, 0, 0.8)",
                  fontFamily: "var(--font-orbitron), 'Arial Black', sans-serif",
                  letterSpacing: "0.15em"
                }}
              >
                  <p className='text-amber-100'>
                      Username
                  </p>
              </Label>
              <Input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={isLoading}
                  className="h-12 bg-black/20 backdrop-blur-sm border-2 rounded-lg text-white placeholder:text-orange-200/60 focus:ring-0 transition-all font-semibold input-shine"
                  style={{
                    fontFamily: "var(--font-orbitron), monospace",
                    // TO CHANGE BORDER COLOR: Change rgba(251, 145, 88, ...) values
                    // Current: rgba(251, 145, 88, 0.79) - Orange/Coral
                    // Gold: rgba(212, 175, 55, 0.6) | Blue: rgba(59, 130, 246, 0.6) | Red: rgba(239, 68, 68, 0.6)
                    borderColor: "rgba(251, 145, 88, 0.79)",
                    // Override focus border
                    outline: "none",
                  }}
                  onFocus={(e) => {
                    e.target.style.borderColor = "rgba(251, 145, 88, 1)";
                  }}
                  onBlur={(e) => {
                    e.target.style.borderColor = "rgba(251, 145, 88, 0.79)";
                  }}
              />
            </div>
            <div className="space-y-3">
              <Label 
                htmlFor="password" 
                className="text-white font-bold text-lg tracking-wider uppercase"
                style={{
                  textShadow: "0 0 10px rgba(212, 175, 55, 0.8), 0 2px 4px rgba(0, 0, 0, 0.8)",
                  fontFamily: "var(--font-orbitron), 'Arial Black', sans-serif",
                  letterSpacing: "0.15em"
                }}
              >
                <p className='text-amber-100'>
                    Password
                </p>
              </Label>
              <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  disabled={isLoading}
                  className="h-12 bg-black/20 backdrop-blur-sm border-2 rounded-lg text-white placeholder:text-orange-200/60 focus:ring-0 transition-all font-semibold input-shine"
                  style={{
                    fontFamily: "var(--font-orbitron), monospace",
                    // TO CHANGE BORDER COLOR: Change rgba(251, 145, 88, ...) values
                    // Current: rgba(251, 145, 88, 0.79) - Orange/Coral
                    // Gold: rgba(212, 175, 55, 0.6) | Blue: rgba(59, 130, 246, 0.6) | Red: rgba(239, 68, 68, 0.6)
                    borderColor: "rgba(251, 145, 88, 0.79)",
                    // Override focus border
                    outline: "none",
                  }}
                  onFocus={(e) => {
                    e.target.style.borderColor = "rgba(251, 145, 88, 1)";
                  }}
                  onBlur={(e) => {
                    e.target.style.borderColor = "rgba(251, 145, 88, 0.79)";
                  }}
              />
            </div>

            {/* K_user:管理员带外发给你的 16 字节密钥(32 个十六进制字符),每周轮换。
                本地已缓存就不再要求填写,只留一个更换入口。 */}
            {needKUser ? (
              <div className="space-y-3">
                <Label
                  htmlFor="kuser"
                  className="text-white font-bold text-lg tracking-wider uppercase"
                  style={{
                    textShadow: "0 0 10px rgba(212, 175, 55, 0.8), 0 2px 4px rgba(0, 0, 0, 0.8)",
                    fontFamily: "var(--font-orbitron), 'Arial Black', sans-serif",
                    letterSpacing: "0.15em"
                  }}
                >
                  <p className='text-amber-100'>
                      K_user 密钥
                  </p>
                </Label>
                <Input
                    id="kuser"
                    type="password"
                    value={kUser}
                    onChange={(e) => setKUser(e.target.value)}
                    required
                    disabled={isLoading}
                    placeholder="管理员发给你的 32 位十六进制"
                    className="h-12 bg-black/20 backdrop-blur-sm border-2 rounded-lg text-white placeholder:text-orange-200/60 focus:ring-0 transition-all font-semibold input-shine"
                    style={{
                      fontFamily: "var(--font-orbitron), monospace",
                      borderColor: "rgba(251, 145, 88, 0.79)",
                      outline: "none",
                    }}
                    onFocus={(e) => {
                      e.target.style.borderColor = "rgba(251, 145, 88, 1)";
                    }}
                    onBlur={(e) => {
                      e.target.style.borderColor = "rgba(251, 145, 88, 0.79)";
                    }}
                />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setNeedKUser(true)}
                className="text-sm text-amber-200/70 hover:text-amber-100 underline underline-offset-4"
              >
                换一把 K_user 密钥
              </button>
            )}

            {notice && (
              <div className="p-3 text-sm bg-amber-900/70 backdrop-blur-md border-2 border-amber-500/50 rounded-lg text-amber-50 font-semibold">
                {notice}
              </div>
            )}

            <Button
                type="submit"
                className="w-full h-16 text-xl font-black uppercase tracking-widest relative overflow-hidden group transition-all duration-300"
                style={{
                  // TO CHANGE BUTTON BACKGROUND: Change the gradient colors
                  // Transparent: background: "rgba(0, 0, 0, 0.2)"
                  // Solid: "linear-gradient(135deg, #f6f343 0%, #f9f75a 50%, #f6f343 100%)"
                  background: "rgba(0, 0, 0, 0.2)",
                  backdropFilter: "blur(8px)",
                  // TO CHANGE BORDER COLOR: #f6f343 (Yellow) = rgba(246, 243, 67, ...)
                  // Current: #f6f343 | Gold: rgba(212, 175, 55, 0.8) | Blue: rgba(59, 130, 246, 0.8)
                  border: "2px solid #f6f343",
                  boxShadow: "0 0 30px rgba(246, 243, 67, 0.6), 0 0 60px rgba(246, 243, 67, 0.3), inset 0 0 20px rgba(246, 243, 67, 0.2)",
                  textShadow: "0 0 10px rgba(246, 243, 67, 0.8), 0 2px 4px rgba(0, 0, 0, 0.8)",
                  fontFamily: "var(--font-orbitron), 'Arial Black', sans-serif",
                  color: "#f6f343",
                  transform: "perspective(1000px)",
                }}
                onMouseEnter={(e) => {
                  setIsHovered(true)
                  // Hover color - brighter yellow
                  e.currentTarget.style.border = "2px solid #fffca8"
                  e.currentTarget.style.boxShadow = "0 0 40px rgba(255, 252, 168, 0.8), 0 0 80px rgba(255, 252, 168, 0.5), inset 0 0 30px rgba(255, 252, 168, 0.3)"
                  e.currentTarget.style.textShadow = "0 0 15px rgba(255, 252, 168, 1), 0 2px 4px rgba(0, 0, 0, 0.8)"
                  e.currentTarget.style.color = "#fffca8"
                }}
                onMouseLeave={(e) => {
                  setIsHovered(false)
                  // Reset to original color #f6f343
                  e.currentTarget.style.border = "2px solid #f6f343"
                  e.currentTarget.style.boxShadow = "0 0 30px rgba(246, 243, 67, 0.6), 0 0 60px rgba(246, 243, 67, 0.3), inset 0 0 20px rgba(246, 243, 67, 0.2)"
                  e.currentTarget.style.textShadow = "0 0 10px rgba(246, 243, 67, 0.8), 0 2px 4px rgba(0, 0, 0, 0.8)"
                  e.currentTarget.style.color = "#f6f343"
                }}
                disabled={isLoading}
                onMouseMove={(e) => {
                  if (isHovered && !isLoading) {
                    const rect = e.currentTarget.getBoundingClientRect()
                    const x = ((e.clientX - rect.left) / rect.width) * 100
                    const y = ((e.clientY - rect.top) / rect.height) * 100
                    // Hover effect with brighter yellow
                    e.currentTarget.style.background = `radial-gradient(circle at ${x}% ${y}%, rgba(255, 252, 168, 0.3) 0%, rgba(246, 243, 67, 0.2) 50%, rgba(249, 247, 90, 0.2) 100%)`
                  }
                }}
            >
              <span className="relative z-10">{isLoading ? 'Loading...' : 'Enter'}</span>
              {isHovered && !isLoading && (
                <div 
                  className="absolute inset-0"
                  style={{
                    background: "linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.3) 50%, transparent 100%)",
                    animation: "shimmer 2s infinite",
                  }}
                />
              )}
            </Button>
          </form>

          {/* Forget Password */}
          <div className="text-center pt-6">
            <button 
              className="text-sm text-amber-300 hover:text-amber-200 font-semibold tracking-wide transition-all uppercase"
              style={{
                textShadow: "0 0 8px rgba(212, 175, 55, 0.6), 0 2px 4px rgba(0, 0, 0, 0.8)",
                fontFamily: "var(--font-orbitron), sans-serif",
                letterSpacing: "0.1em"
              }}
            >
              忘记密码?太笨！
            </button>
          </div>
        </div>
      </div>
  )
}
