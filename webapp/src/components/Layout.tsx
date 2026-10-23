import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import symbolUrl from '../assets/symbol.svg'
import { useAuth } from '../auth'
import { useOnline } from '../hooks'
import { Icon, type IconName } from './Icon'

interface LayoutProps {
  title: string
  back?: boolean
  actions?: ReactNode
  /** 화면 하단에 붙는 버튼 영역 (탭바 대신) */
  footer?: ReactNode
  /** 기본: 뒤로 가기가 없는 화면에만 탭바 */
  tabbar?: boolean
  contentClassName?: string
  children: ReactNode
}

export function Logo({ size = 28 }: { size?: number }) {
  return <img src={symbolUrl} alt="" width={size} height={size} />
}

export function Layout({
  title,
  back,
  actions,
  footer,
  tabbar = !back && !footer,
  contentClassName,
  children,
}: LayoutProps) {
  const navigate = useNavigate()
  const online = useOnline()
  const { me } = useAuth()

  return (
    <div className={`kb-shell${tabbar ? ' has-tabbar' : ''}`}>
      <header className={`kb-topbar${back ? ' kb-topbar--back' : ''}`}>
        {back ? (
          <button
            className="kb-icon-btn"
            // 알림 등으로 바로 들어온 경우 돌아갈 화면이 없으면 홈으로
            onClick={() => (window.history.state?.idx > 0 ? navigate(-1) : navigate('/'))}
            aria-label="뒤로"
          >
            <Icon name="back" />
          </button>
        ) : (
          <span className="kb-topbar__logo" aria-hidden="true">
            <Logo />
          </span>
        )}
        <h1>{title}</h1>
        {actions}
      </header>

      {!online && (
        <div className="kb-offline" role="status">
          <Icon name="wifiOff" size={16} /> 오프라인 상태입니다. 연결되면 자동으로 갱신됩니다.
        </div>
      )}

      <main className={`kb-content${contentClassName ? ` ${contentClassName}` : ''}`}>{children}</main>

      {footer && (
        <div className="kb-actionbar">
          <div className="kb-actionbar__inner">{footer}</div>
        </div>
      )}

      {tabbar && (
        <nav className="kb-tabbar" aria-label="메뉴">
          <Tab to="/" icon="home" label="홈" end />
          <Tab to="/new" icon="plus" label="새 예약" />
          {me?.user.is_admin && <Tab to="/admin" icon="shield" label="관리" />}
          <Tab to="/settings" icon="settings" label="설정" />
        </nav>
      )}
    </div>
  )
}

function Tab({ to, icon, label, end }: { to: string; icon: IconName; label: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `kb-tab${isActive ? ' is-active' : ''}`}>
      <Icon name={icon} size={22} />
      <span>{label}</span>
    </NavLink>
  )
}
