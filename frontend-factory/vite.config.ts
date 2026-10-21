import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },
  server: {
    port: 8080,
    proxy: {
      '/api': { target: process.env.FACTORY_API_PROXY || 'http://127.0.0.1:8011', changeOrigin: true }
    }
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1500 }
})
