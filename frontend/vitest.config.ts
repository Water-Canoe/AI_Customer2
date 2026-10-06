import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'


export default defineConfig({
  // public 根路径由浏览器提供，测试中保留原地址，避免 Windows 文件 URL 转换。
  plugins: [vue({ template: { transformAssetUrls: { includeAbsolute: false } } })],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    restoreMocks: true,
  },
})
