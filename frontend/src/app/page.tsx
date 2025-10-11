'use client'

import { useState } from 'react'
import { Card, Heart, Spade, Diamond, Club } from 'lucide-react'

export default function Home() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)

  if (!isLoggedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="max-w-md w-full mx-4">
          <div className="card p-8">
            <div className="text-center mb-8">
              <div className="flex justify-center mb-4">
                <div className="flex space-x-2">
                  <Heart className="w-8 h-8 text-red-500" />
                  <Spade className="w-8 h-8 text-black" />
                  <Diamond className="w-8 h-8 text-red-500" />
                  <Club className="w-8 h-8 text-black" />
                </div>
              </div>
              <h1 className="text-3xl font-bold text-poker-green mb-2">扑克平台</h1>
              <p className="text-gray-600">专业的在线扑克游戏平台</p>
            </div>
            
            <div className="space-y-4">
              <button 
                className="btn-primary w-full"
                onClick={() => setIsLoggedIn(true)}
              >
                开始游戏
              </button>
              <button className="btn-secondary w-full">
                注册账号
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen p-4">
      <header className="mb-8">
        <div className="flex justify-between items-center">
          <h1 className="text-2xl font-bold">扑克平台</h1>
          <div className="flex items-center space-x-4">
            <div className="chip bg-poker-gold text-poker-black">
              <span>1000</span>
            </div>
            <button 
              className="btn-secondary"
              onClick={() => setIsLoggedIn(false)}
            >
              退出
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 游戏区域 */}
          <div className="lg:col-span-2">
            <div className="card p-6">
              <h2 className="text-xl font-semibold mb-4">游戏桌</h2>
              <div className="bg-poker-green rounded-lg p-8 min-h-96 flex items-center justify-center">
                <div className="text-center">
                  <div className="flex justify-center space-x-2 mb-4">
                    <div className="poker-card">A♠</div>
                    <div className="poker-card">K♥</div>
                    <div className="poker-card">Q♦</div>
                    <div className="poker-card">J♣</div>
                    <div className="poker-card">10♠</div>
                  </div>
                  <p className="text-white">公共牌区域</p>
                </div>
              </div>
            </div>
          </div>

          {/* 侧边栏 */}
          <div className="space-y-6">
            {/* 玩家信息 */}
            <div className="card p-4">
              <h3 className="font-semibold mb-3">玩家信息</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span>用户名:</span>
                  <span className="font-medium">玩家1</span>
                </div>
                <div className="flex justify-between">
                  <span>筹码:</span>
                  <span className="font-medium text-poker-gold">1000</span>
                </div>
                <div className="flex justify-between">
                  <span>位置:</span>
                  <span className="font-medium">庄家</span>
                </div>
              </div>
            </div>

            {/* 游戏控制 */}
            <div className="card p-4">
              <h3 className="font-semibold mb-3">游戏控制</h3>
              <div className="space-y-2">
                <button className="btn-primary w-full">跟注</button>
                <button className="btn-secondary w-full">加注</button>
                <button className="btn-secondary w-full">弃牌</button>
                <button className="btn-secondary w-full">全下</button>
              </div>
            </div>

            {/* 聊天区域 */}
            <div className="card p-4">
              <h3 className="font-semibold mb-3">聊天</h3>
              <div className="h-32 bg-gray-50 rounded p-2 mb-2 overflow-y-auto">
                <div className="text-sm text-gray-600">
                  <p>欢迎来到扑克平台！</p>
                  <p>祝您游戏愉快！</p>
                </div>
              </div>
              <div className="flex space-x-2">
                <input 
                  type="text" 
                  placeholder="输入消息..."
                  className="flex-1 px-3 py-1 border border-gray-300 rounded text-sm"
                />
                <button className="btn-primary text-sm px-3 py-1">发送</button>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}