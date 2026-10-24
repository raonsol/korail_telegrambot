import { defineConfig, minimal2023Preset } from '@vite-pwa/assets-generator/config'

// npm run generate-icons: public/icon.svg로 PWA 아이콘 생성
const background = '#1a56d6'

export default defineConfig({
  preset: {
    ...minimal2023Preset,
    // 기본값(quality 60)은 팔레트 PNG로 줄여 가장자리가 계단처럼 깨짐 → 풀컬러로 저장
    png: { compressionLevel: 9, palette: false },
    // icon.svg에 이미 여백이 있음. 마스커블은 원형 안전 영역(지름 80%)에 들도록 10%만 줄임
    maskable: { ...minimal2023Preset.maskable, padding: 0.1, resizeOptions: { background } },
    // iOS는 모서리를 직접 둥글게 자르므로 여백 없이 꽉 채움 (투명 모서리는 배경색으로 채워짐)
    apple: { ...minimal2023Preset.apple, padding: 0, resizeOptions: { background } },
  },
  images: ['public/icon.svg'],
})
