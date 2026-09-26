/// <reference lib="webworker" />
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'

declare let self: ServiceWorkerGlobalScope

// 앱 셸 precache → 오프라인에서도 앱이 열림. /api/* 는 캐시하지 않음 (network only)
precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('/app/index.html'), {
    allowlist: [/^\/app\//],
  }),
)

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting()
})

interface PushPayload {
  title?: string
  body?: string
  url?: string
  tag?: string
}

self.addEventListener('push', (event) => {
  let payload: PushPayload = {}
  try {
    payload = event.data?.json() ?? {}
  } catch {
    payload = { body: event.data?.text() }
  }
  const title = payload.title ?? '코레일 예약'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body,
      tag: payload.tag,
      icon: '/app/pwa-192x192.png',
      badge: '/app/pwa-64x64.png',
      data: { url: payload.url ?? '/app/' },
      // 예약 성공은 결제 기한(20분)이 있으므로 사용자가 닫을 때까지 유지
      requireInteraction: title.includes('성공'),
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url: string = event.notification.data?.url ?? '/app/'
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of windows) {
        if (new URL(client.url).pathname.startsWith('/app')) {
          client.postMessage({ type: 'NAVIGATE', url })
          return client.focus()
        }
      }
      return self.clients.openWindow(url)
    })(),
  )
})
