import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { Api, ApiError, setCsrfToken, type Me } from './api'
import { readJson, removeKey, writeJson } from './storage'

interface AuthContextValue {
  me: Me | null
  loading: boolean
  login: (phone: string, password: string, remember: boolean) => Promise<void>
  adminLogin: (password: string, remember: boolean) => Promise<void>
  logout: () => Promise<void>
  setMe: (me: Me) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export const ME_KEY = ['me']

// 오프라인에서도 설치된 앱이 열리도록 마지막 사용자 정보를 보관 (CSRF 토큰 제외)
const ME_SNAPSHOT_KEY = 'me-snapshot'

function saveSnapshot(me: Me) {
  writeJson(ME_SNAPSHOT_KEY, { ...me, csrf_token: '' })
}

export function clearSnapshot() {
  removeKey(ME_SNAPSHOT_KEY)
}

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await Api.me()
    setCsrfToken(me.csrf_token)
    saveSnapshot(me)
    return me
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      setCsrfToken(null)
      clearSnapshot()
      return null
    }
    if (e instanceof ApiError && e.status === 0) {
      const snapshot = readJson<Me | null>(ME_SNAPSHOT_KEY, null)
      if (snapshot) return snapshot
    }
    throw e
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { data, isPending } = useQuery({
    queryKey: ME_KEY,
    queryFn: fetchMe,
    staleTime: 5 * 60 * 1000,
    // 오프라인 스냅샷으로 열린 경우 재연결 시 실제 세션 확인
    refetchOnReconnect: 'always',
    retry: (count, error) => !(error instanceof ApiError) && count < 2,
  })

  const setMe = useCallback(
    (me: Me) => {
      setCsrfToken(me.csrf_token)
      saveSnapshot(me)
      queryClient.setQueryData(ME_KEY, me)
    },
    [queryClient],
  )

  const login = useCallback(
    async (phone: string, password: string, remember: boolean) => {
      setMe(await Api.login(phone, password, remember))
    },
    [setMe],
  )

  const adminLogin = useCallback(
    async (password: string, remember: boolean) => {
      setMe(await Api.adminLogin(password, remember))
    },
    [setMe],
  )

  const logout = useCallback(async () => {
    try {
      await Api.logout()
    } finally {
      setCsrfToken(null)
      clearSnapshot()
      queryClient.clear()
      queryClient.setQueryData(ME_KEY, null)
    }
  }, [queryClient])

  const value = useMemo(
    () => ({ me: data ?? null, loading: isPending, login, adminLogin, logout, setMe }),
    [data, isPending, login, adminLogin, logout, setMe],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
