import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ApiError } from './api'
import { App } from './App'
import { AuthProvider, ME_KEY, clearSnapshot } from './auth'
import './styles.css'

// 세션이 만료되면(401) 로그인 화면으로
function onError(error: unknown) {
  if (error instanceof ApiError && error.status === 401 && error.code !== 'LOGIN_FAILED') {
    clearSnapshot()
    queryClient.setQueryData(ME_KEY, null)
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      staleTime: 10_000,
      // 오프라인이면 멈춰 있지 않고 "연결할 수 없음"을 표시, 재연결 시 자동 갱신
      networkMode: 'offlineFirst',
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/app">
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
