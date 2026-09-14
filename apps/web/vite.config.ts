import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { sourcemap: true },
  test: { environment: 'jsdom', globals: true, exclude: ['e2e/**', 'node_modules/**', 'dist/**'] },
})
