import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// FastAPI가 /app 경로에서 빌드 결과를 서빙합니다 (web/static.py)
export default defineConfig({
  base: '/app/',
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'prompt',
      injectRegister: false,
      manifest: {
        name: '코레일 예약',
        short_name: '코레일 예약',
        description: '매진된 KTX 좌석을 자동으로 예약합니다',
        lang: 'ko',
        start_url: '/app/',
        scope: '/app/',
        display: 'standalone',
        background_color: '#f4f6fa',
        theme_color: '#0b4ea2',
        icons: [
          { src: 'pwa-64x64.png', sizes: '64x64', type: 'image/png' },
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'maskable-icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico}'],
      },
    }),
  ],
  server: {
    port: 5173,
    // make webapp-dev: API는 로컬 FastAPI(8390)로 프록시
    proxy: {
      '/api': { target: 'http://localhost:8390', changeOrigin: false },
    },
  },
})
