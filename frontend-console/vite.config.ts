import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },
  server: {
    port: 8080,
    proxy: {
      '/api': { target: 'http://localhost:8001', changeOrigin: true }
    }
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1500 }
})
