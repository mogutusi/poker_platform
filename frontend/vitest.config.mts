// vitest 不读 tsconfig 的 paths,要在这里把 @/ 别名再声明一遍,
// 否则测试里凡是走 @/ 的 import 都会报 "Cannot find package"。

import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    include: ['src/**/*.test.ts'],
  },
})
