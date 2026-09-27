import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { useOnline } from '../hooks'
import { Icon, type IconName } from './Icon'

interface LayoutProps {
  title: string
  back?: boolean
  actions?: ReactNode
  children: ReactNode
}

export function Layout({ title, back, actions, children }: LayoutProps) {
  const navigate = useNavigate()
  const online = useOnline()
  const { me } = useAuth()

  return (
    <div className="shell">
      <header className="topbar">
        {back ? (
          <button className="icon-button" onClick={() => navigate(-1)} aria-label="뒤로">
            <Icon name="back" />
          </button>
        ) : (
          <span className="topbar-logo" aria-hidden="true">
            <img src="/app/icon.svg" alt="" width={28} height={28} />
          </span>
        )}
        <h1>{title}</h1>
        <div className="topbar-actions">{actions}</div>
      </header>

      {!online && (
        <div className="offline-banner" role="status">
          <Icon name="wifiOff" size={16} /> 오프라인 상태입니다. 연결되면 자동으로 갱신됩니다.
        </div>
      )}

      <main className="content">{children}</main>

      <nav className="tabbar" aria-label="메뉴">
        <Tab to="/" icon="home" label="홈" end />
        <Tab to="/new" icon="plus" label="새 예약" />
        {me?.user.is_admin && <Tab to="/admin" icon="shield" label="관리" />}
        <Tab to="/settings" icon="settings" label="설정" />
      </nav>
    </div>
  )
}

function Tab({ to, icon, label, end }: { to: string; icon: IconName; label: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `tab${isActive ? ' active' : ''}`}>
      <Icon name={icon} size={22} />
      <span>{label}</span>
    </NavLink>
  )
}
