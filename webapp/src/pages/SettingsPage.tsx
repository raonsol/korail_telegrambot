import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Api } from '../api'
import { useAuth } from '../auth'
import { ErrorBox } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { useInstallPrompt } from '../hooks'
import {
  getPushState,
  isIos,
  isStandalone,
  subscribePush,
  unsubscribePush,
  type PushState,
} from '../push'

export function SettingsPage() {
  const { me, logout, setMe } = useAuth()
  const navigate = useNavigate()
  const { canInstall, install } = useInstallPrompt()
  const [pushState, setPushState] = useState<PushState | null>(null)
  const [pushBusy, setPushBusy] = useState(false)
  const [pushError, setPushError] = useState<unknown>(null)

  useEffect(() => {
    getPushState().then(setPushState)
  }, [])

  const telegram = useMutation({
    mutationFn: (enabled: boolean) => Api.updateSettings({ telegram_notify: enabled }),
    onSuccess: setMe,
  })

  if (!me) return null
  const { user } = me

  const togglePush = async () => {
    setPushBusy(true)
    setPushError(null)
    try {
      if (pushState === 'subscribed') setPushState(await unsubscribePush())
      else if (me.push_public_key) setPushState(await subscribePush(me.push_public_key))
    } catch (e) {
      setPushError(e)
    } finally {
      setPushBusy(false)
    }
  }

  const needsIosInstall = isIos() && !isStandalone()

  return (
    <Layout title="설정">
      <section className="card account">
        <Icon name={user.is_admin ? 'shield' : 'user'} size={28} />
        <div>
          <strong>{user.is_admin ? '관리자' : user.name || user.phone}</strong>
          {!user.is_admin && <div className="muted small">{user.phone}</div>}
        </div>
      </section>

      <section className="section">
        <h2>알림</h2>
        <div className="card settings-list">
          <div className="setting">
            <div>
              <strong>푸시 알림</strong>
              <p className="muted small">
                {!me.push_enabled
                  ? '서버에 푸시 알림이 설정되지 않았습니다.'
                  : pushState === 'unsupported'
                    ? needsIosInstall
                      ? 'iPhone은 홈 화면에 앱을 추가해야 알림을 받을 수 있습니다.'
                      : '이 브라우저는 푸시 알림을 지원하지 않습니다.'
                    : pushState === 'denied'
                      ? '알림이 차단되어 있습니다. 브라우저 설정에서 허용해주세요.'
                      : '앱을 닫아도 예약 결과를 알려드립니다.'}
              </p>
            </div>
            <Toggle
              checked={pushState === 'subscribed'}
              disabled={
                !me.push_enabled || pushBusy || pushState === 'unsupported' || pushState === 'denied'
              }
              onChange={togglePush}
              label="푸시 알림"
            />
          </div>
          {!user.is_admin && (
            <div className="setting">
              <div>
                <strong>텔레그램 알림</strong>
                <p className="muted small">
                  {user.telegram_linked
                    ? '웹에서 시작한 예약 결과도 텔레그램으로 받습니다.'
                    : '텔레그램 봇에서 한 번 로그인하면 연결됩니다.'}
                </p>
              </div>
              <Toggle
                checked={user.telegram_linked && user.telegram_notify}
                disabled={!user.telegram_linked || telegram.isPending}
                onChange={() => telegram.mutate(!user.telegram_notify)}
                label="텔레그램 알림"
              />
            </div>
          )}
        </div>
        <ErrorBox error={pushError ?? telegram.error} />
      </section>

      {!isStandalone() && (
        <section className="section">
          <h2>앱 설치</h2>
          <div className="card">
            {canInstall ? (
              <button className="button block" onClick={install}>
                <Icon name="download" size={18} /> 홈 화면에 설치
              </button>
            ) : needsIosInstall ? (
              <p className="small">
                Safari 하단의 <strong>공유</strong> 버튼 → <strong>홈 화면에 추가</strong>를 누르면
                앱처럼 사용할 수 있고 푸시 알림도 받을 수 있습니다.
              </p>
            ) : (
              <p className="small muted">
                브라우저 메뉴의 <strong>앱 설치</strong> 또는 <strong>홈 화면에 추가</strong>를
                이용해주세요.
              </p>
            )}
          </div>
        </section>
      )}

      <button
        className="button ghost block logout"
        onClick={async () => {
          await logout()
          navigate('/login', { replace: true })
        }}
      >
        <Icon name="logout" size={18} /> 로그아웃
      </button>
      <p className="fineprint center">로그아웃하면 서버에 보관된 코레일 비밀번호가 삭제됩니다.</p>
    </Layout>
  )
}

function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean
  disabled?: boolean
  onChange: () => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`toggle${checked ? ' on' : ''}`}
      disabled={disabled}
      onClick={onChange}
    >
      <span />
    </button>
  )
}
