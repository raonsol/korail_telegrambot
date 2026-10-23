// 화면 테마: 시스템 설정을 따르거나 라이트/다크 고정 (<html data-theme>, 기기별 localStorage)
import { useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'

// index.html의 초기 스크립트와 같은 키
const THEME_KEY = 'theme'
const THEME_COLORS = { light: '#f6f8fb', dark: '#0a0f18' }
const media = () => window.matchMedia('(prefers-color-scheme: dark)')

export function readThemePref(): ThemePref {
  try {
    const value = localStorage.getItem(THEME_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(pref: ThemePref = readThemePref()) {
  const theme = pref === 'system' ? (media().matches ? 'dark' : 'light') : pref
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLORS[theme])
}

/** 시스템 테마가 바뀌면 따라감 (설정이 '시스템'일 때만) */
export function watchSystemTheme() {
  applyTheme()
  media().addEventListener('change', () => {
    if (readThemePref() === 'system') applyTheme('system')
  })
}

export function useThemePref(): [ThemePref, (pref: ThemePref) => void] {
  const [pref, setPref] = useState(readThemePref)
  useEffect(() => {
    try {
      if (pref === 'system') localStorage.removeItem(THEME_KEY)
      else localStorage.setItem(THEME_KEY, pref)
    } catch {
      // private mode 등 — 이번 방문에만 적용
    }
    applyTheme(pref)
  }, [pref])
  return [pref, setPref]
}
