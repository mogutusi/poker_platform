// 浏览器端到端测试的配置。只跑 chromium:内网自用,不做跨浏览器兼容矩阵。
//
// 后端不由这里启动——它有自己的库和状态,应当由人显式起(见 docs/dev.md),
// 免得测试悄悄改动开发中的后端数据。

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  // 几个用例会共用后端状态(同一批 dev 账号),串行跑最省心。
  workers: 1,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:3000',
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
