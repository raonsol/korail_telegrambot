import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// npm run generate-icons: public/icon.svg로 PWA 아이콘 생성
const background = '#0b4ea2'

export default defineConfig({
  preset: {
    ...minimal2023Preset,
    maskable: { ...minimal2023Preset.maskable, resizeOptions: { background } },
    apple: { ...minimal2023Preset.apple, resizeOptions: { background } },
  },
  images: ['public/icon.svg'],
})
