import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { Reservation } from './api'

/** SSE로 예약 상태 변경을 받아 캐시 갱신 (연결이 끊기면 브라우저가 자동 재연결) */
export function useReservationEvents(enabled: boolean) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return
    const source = new EventSource('/api/events')

    source.addEventListener('reservation', (event) => {
      const reservation = JSON.parse((event as MessageEvent).data) as Reservation
      queryClient.setQueryData(['reservation', reservation.id], reservation)
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
    })
    // 재연결 시 놓친 이벤트 보정
    source.addEventListener('open', () => {
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
    })

    return () => source.close()
  }, [enabled, queryClient])
}

export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

/** 안드로이드/데스크톱 크롬의 설치 프롬프트 */
export function useInstallPrompt() {
  const [event, setEvent] = useState<BeforeInstallPromptEvent | null>(null)

  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault()
      setEvent(e as BeforeInstallPromptEvent)
    }
    const installed = () => setEvent(null)
    window.addEventListener('beforeinstallprompt', handler)
    window.addEventListener('appinstalled', installed)
    return () => {
      window.removeEventListener('beforeinstallprompt', handler)
      window.removeEventListener('appinstalled', installed)
    }
  }, [])

  const install = async () => {
    if (!event) return
    await event.prompt()
    await event.userChoice
    setEvent(null)
  }

  return { canInstall: event !== null, install }
}
