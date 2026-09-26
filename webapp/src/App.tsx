import { useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './auth'
import { Spinner } from './components/Feedback'
import { UpdatePrompt } from './components/UpdatePrompt'
import { useReservationEvents } from './hooks'
import { AdminPage } from './pages/AdminPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { NewReservationPage } from './pages/NewReservationPage'
import { ReservationDetailPage } from './pages/ReservationDetailPage'
import { SettingsPage } from './pages/SettingsPage'

function RequireAuth({ children, admin }: { children: ReactNode; admin?: boolean }) {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner />
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (admin && !me.user.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
}

/** 알림 클릭 시 서비스 워커가 보낸 경로로 이동 */
function useServiceWorkerNavigation() {
  const navigate = useNavigate()
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return
    const handler = (event: MessageEvent) => {
      if (event.data?.type === 'NAVIGATE' && typeof event.data.url === 'string') {
        navigate(event.data.url.replace(/^\/app/, '') || '/')
      }
    }
    navigator.serviceWorker.addEventListener('message', handler)
    return () => navigator.serviceWorker.removeEventListener('message', handler)
  }, [navigate])
}

export function App() {
  const { me } = useAuth()
  useReservationEvents(Boolean(me))
  useServiceWorkerNavigation()

  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <HomePage />
            </RequireAuth>
          }
        />
        <Route
          path="/new"
          element={
            <RequireAuth>
              <NewReservationPage />
            </RequireAuth>
          }
        />
        <Route
          path="/r/:id"
          element={
            <RequireAuth>
              <ReservationDetailPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings"
          element={
            <RequireAuth>
              <SettingsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin"
          element={
            <RequireAuth admin>
              <AdminPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <UpdatePrompt />
    </>
  )
}
