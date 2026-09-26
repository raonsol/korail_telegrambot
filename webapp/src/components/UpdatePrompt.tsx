import { useRegisterSW } from 'virtual:pwa-register/react'

/** 새 버전 배포 시 새로고침 안내 */
export function UpdatePrompt() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_url, registration) {
      // 앱을 오래 켜두는 경우를 위해 1시간마다 업데이트 확인
      if (registration) setInterval(() => registration.update(), 60 * 60 * 1000)
    },
  })

  if (!needRefresh) return null
  return (
    <div className="toast" role="status">
      <span>새 버전이 있습니다.</span>
      <button className="button small" onClick={() => updateServiceWorker(true)}>
        새로고침
      </button>
      <button className="button small ghost" onClick={() => setNeedRefresh(false)}>
        나중에
      </button>
    </div>
  )
}
