// 인라인 SVG 아이콘 (외부 폰트/라이브러리 없이)
const PATHS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  plus: 'M12 5v14M5 12h14',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.4 7.4 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.4 7.4 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.4 7.4 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z',
  shield: 'M12 3 4 6v6c0 5 3.4 8.4 8 9 4.6-.6 8-4 8-9V6z',
  back: 'M15 18l-6-6 6-6',
  swap: 'M7 4v16M7 4 4 7M7 4l3 3M17 20V4m0 16-3-3m3 3 3-3',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  check: 'M5 12.5 10 17 19 7',
  x: 'M6 6l12 12M18 6 6 18',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8zm4 12a2 2 0 0 0 4 0',
  logout: 'M15 3h4a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1h-4M10 17l-5-5 5-5M5 12h11',
  download: 'M12 4v11m0 0-4-4m4 4 4-4M5 20h14',
  search: 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zm9 2-4.3-4.3',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  wifiOff: 'M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M12 20h.01M5 12.5a10 10 0 0 1 4-2.3M19 12.5a10 10 0 0 0-2.6-1.8M2 8.8a15 15 0 0 1 4.5-2.7M22 8.8A15 15 0 0 0 11 5',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-8 9a8 8 0 0 1 16 0',
  trash: 'M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
