"use client"

import type React from "react"
import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiClient } from "@/lib/api"
import pokerRoomBg from "@/pics/poker-room.png"

export default function PokerLoginPage() {
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [isHovered, setIsHovered] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    try {
      // Mock login for testing: admin / 123456
      const isMockLogin = username === "admin" && password === "123456"
      
      if (isMockLogin) {
        // Simulate API delay
        await new Promise(resolve => setTimeout(resolve, 500))
        
        // Mock successful response
        const mockToken = `mock_token_${Date.now()}`
        if (typeof window !== "undefined") {
          localStorage.setItem("auth_token", mockToken)
          localStorage.setItem("player_name", username)
        }
        router.push("/lobby")
        return
      }

      // Real API call for other credentials
      const response = await apiClient.login(username, password)
      // Store token and basic player info
      if (response.token) {
        if (typeof window !== "undefined") {
          localStorage.setItem("auth_token", response.token)
          localStorage.setItem("player_name", username)
        }
        router.push("/lobby")
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "登录失败，请检查用户名和密码"
      setError(errorMessage)
      console.error("Login error:", err)
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
