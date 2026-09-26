// 기기별 편의 기능용 localStorage (실패해도 앱은 정상 동작)

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

export function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // private mode 등
  }
}

export function removeKey(key: string) {
  try {
    localStorage.removeItem(key)
  } catch {
    // ignore
  }
}

const RECENT_STATIONS_KEY = 'recent-stations'

export function recentStations(): string[] {
  return readJson<string[]>(RECENT_STATIONS_KEY, [])
}

export function rememberStations(...names: string[]) {
  const merged = [...names, ...recentStations().filter((n) => !names.includes(n))]
  writeJson(RECENT_STATIONS_KEY, merged.slice(0, 8))
}
