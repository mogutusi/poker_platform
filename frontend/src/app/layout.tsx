import type { Metadata } from 'next'
import { Inter, Orbitron } from 'next/font/google'
import '@/styles/globals.css'

const inter = Inter({ subsets: ['latin'] })
const orbitron = Orbitron({ 
  subsets: ['latin'],
  variable: '--font-orbitron',
  weight: ['400', '700', '900']
})

export const metadata: Metadata = {
  title: '扑克平台',
  description: '专业的在线扑克游戏平台',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN">
      <body className={`${inter.className} ${orbitron.variable}`}>
        <div className="min-h-screen bg-gradient-to-br from-poker-green to-green-900">
          {children}
        </div>
      </body>
    </html>
  )
}