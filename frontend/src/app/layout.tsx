import type { Metadata } from 'next'
import localFont from 'next/font/local'
import '@/styles/globals.css'

// Fonts are bundled in src/fonts (SIL OFL, see the OFL-*.txt files there) instead of
// next/font/google, so dev and build also work offline or where Google Fonts is unreachable.
// Inter + Orbitron are the original UI fonts; Barlow Condensed is only for the table page's
// action buttons and chip/stack numbers.
const inter = localFont({
  src: [
    { path: '../fonts/inter-latin-400-normal.woff2', weight: '400' },
    { path: '../fonts/inter-latin-500-normal.woff2', weight: '500' },
    { path: '../fonts/inter-latin-600-normal.woff2', weight: '600' },
    { path: '../fonts/inter-latin-700-normal.woff2', weight: '700' },
  ],
  display: 'swap',
})
const orbitron = localFont({
  src: [
    { path: '../fonts/orbitron-latin-400-normal.woff2', weight: '400' },
    { path: '../fonts/orbitron-latin-700-normal.woff2', weight: '700' },
    { path: '../fonts/orbitron-latin-900-normal.woff2', weight: '900' },
  ],
  variable: '--font-orbitron',
  display: 'swap',
})
const barlowCondensed = localFont({
  src: [
    { path: '../fonts/barlow-condensed-latin-500-normal.woff2', weight: '500' },
    { path: '../fonts/barlow-condensed-latin-600-normal.woff2', weight: '600' },
    { path: '../fonts/barlow-condensed-latin-700-normal.woff2', weight: '700' },
  ],
  variable: '--font-barlow-condensed',
  display: 'swap',
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
      <body className={`${inter.className} ${orbitron.variable} ${barlowCondensed.variable}`}>
        <div className="min-h-screen bg-gradient-to-br from-poker-green to-green-900">
          {children}
        </div>
      </body>
    </html>
  )
}
