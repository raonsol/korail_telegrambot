import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorBox } from '../components/Feedback'
import { Logo } from '../components/Layout'
import { formatPhoneInput } from '../format'

type Mode = 'user' | 'admin'

export function LoginPage() {
  const { me, login, adminLogin } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState<Mode>('user')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  const requested = (location.state as { from?: string } | null)?.from
  // 관리자는 따로 요청한 화면이 없으면 관리 화면으로
  const from = requested ?? (me?.user.is_admin || mode === 'admin' ? '/admin' : '/')
  if (me) return <Navigate to={from} replace />

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (mode === 'admin') await adminLogin(password, remember)
      else await login(phone, password, remember)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err)
      setPassword('')
    } finally {
      setSubmitting(false)
    }
  }

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setPassword('')
  }

  return (
    <div className="kb-shell">
      <main className="kb-login">
        <div className="kb-login__brand">
          <Logo size={72} />
          <h1>코레일봇</h1>
          <p>매진된 KTX 좌석이 나오면 자동으로 예약해 드립니다.</p>
        </div>

        <div className="kb-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={mode === 'user'}
            className={mode === 'user' ? 'is-active' : ''}
            onClick={() => switchMode('user')}
          >
            코레일 계정
          </button>
          <button
            role="tab"
            aria-selected={mode === 'admin'}
            className={mode === 'admin' ? 'is-active' : ''}
            onClick={() => switchMode('admin')}
          >
            관리자
          </button>
        </div>

        <form onSubmit={onSubmit} className="kb-form">
          {mode === 'user' && (
            <div className="kb-field">
              <label className="kb-label" htmlFor="phone">
                휴대전화번호 (코레일 ID)
              </label>
              <input
                id="phone"
                className="kb-input kb-input--num"
                type="tel"
                inputMode="numeric"
                autoComplete="username"
                placeholder="010-1234-5678"
                value={phone}
                onChange={(e) => setPhone(formatPhoneInput(e.target.value))}
                required
              />
            </div>
          )}
          <div className="kb-field">
            <label className="kb-label" htmlFor="password">
              {mode === 'admin' ? '관리자 비밀번호' : '코레일 비밀번호'}
            </label>
            <input
              id="password"
              className="kb-input"
              type="password"
              autoComplete={mode === 'admin' ? 'off' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <label className="kb-checkbox">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            로그인 유지
          </label>

          <ErrorBox error={error} />

          <button className="kb-btn kb-btn--primary kb-btn--block kb-btn--lg" disabled={submitting}>
            {submitting ? '코레일 로그인 확인 중…' : '로그인'}
          </button>
        </form>

        <p className="kb-fineprint">
          {mode === 'user'
            ? '등록된 사용자만 이용할 수 있습니다. 코레일 비밀번호는 예약 실행을 위해 서버에 암호화되어 임시 보관되며, 로그아웃하면 삭제됩니다. 코레일은 로그인 5회 실패 시 계정이 잠기므로 3회 실패하면 잠시 로그인이 제한됩니다.'
            : '관리자는 설정된 관리자 코레일 계정으로 예약하며, 사용자와 전체 예약을 관리할 수 있습니다.'}
        </p>
      </main>
    </div>
  )
}
