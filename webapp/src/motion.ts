// 화면 전환 애니메이션 (View Transitions API) — 네이티브 앱처럼 화면이 밀려 들어오고 나감.
//
// BrowserRouter 는 react-router 의 viewTransition 옵션(데이터 라우터 전용)을 쓸 수 없어서
// document.startViewTransition 으로 직접 감싼다. 새 화면이 그려졌다는 신호(routeCommitted)는
// App 의 RouteTransitions 가 위치가 바뀐 직후(useLayoutEffect) 보낸다.
// 지원하지 않는 브라우저(iOS 17 이하 등)에서는 애니메이션 없이 바로 이동한다.

import { useSyncExternalStore } from 'react'

/** push: 새 화면이 오른쪽에서 들어옴 · pop: 오른쪽으로 빠짐 · tab: 하단 탭 전환 (겹쳐 바뀜) */
export type NavDirection = 'push' | 'pop' | 'tab'

interface ViewTransitionLike {
  finished: Promise<void>
  skipTransition(): void
}
type StartViewTransition = (update: () => Promise<void> | void) => ViewTransitionLike

/** 새 화면이 그려지기를 너무 오래 기다리지 않음 (같은 주소로 이동 등) */
const COMMIT_TIMEOUT = 500

let pendingCommit: (() => void) | null = null
let current: ViewTransitionLike | null = null
/** 이동을 요청했지만 아직 새 화면이 그려지지 않음 (빠른 두 번 탭이 두 번 이동하지 않게) */
let navigating = false

export function reducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function startViewTransition(): StartViewTransition | null {
  const doc = document as Document & { startViewTransition?: StartViewTransition }
  if (typeof doc.startViewTransition !== 'function') return null
  // 백그라운드 탭에서는 전환이 바로 취소되므로 그냥 이동
  if (document.visibilityState !== 'visible') return null
  return doc.startViewTransition.bind(document)
}

/** 새 화면이 DOM 에 반영됨 (RouteTransitions 에서 호출) */
export function routeCommitted(): void {
  navigating = false
  pendingCommit?.()
  pendingCommit = null
}

/**
 * navigate 를 화면 전환 애니메이션으로 감쌈.
 * 이동 방향은 <html data-nav> 로 CSS(app.css "화면 전환")에 전달한다.
 */
export function runNavTransition(direction: NavDirection, navigate: () => void): void {
  const start = startViewTransition()
  if (!start) {
    navigate()
    return
  }
  // 앞 이동이 아직 그려지기 전에 또 누름 (카드 두 번 탭 등) - 무시.
  // 앞 전환을 건너뛰어도(skipTransition) 그 이동은 실행되므로 두 번 이동하게 됨
  if (navigating) return
  navigating = true
  current?.skipTransition()
  const root = document.documentElement
  root.dataset.nav = direction
  const transition = start(
    () =>
      new Promise<void>((resolve) => {
        const done = () => {
          navigating = false
          resolve()
        }
        pendingCommit = done
        window.setTimeout(done, COMMIT_TIMEOUT)
        navigate()
      }),
  )
  current = transition
  transition.finished
    .catch(() => undefined)
    .finally(() => {
      if (current !== transition) return
      current = null
      delete root.dataset.nav
      // 상세 → 목록으로 돌아오는 모핑까지 끝났으면 공유 승차권 표시를 지움
      if (direction === 'pop') setSharedTicket(null)
    })
}

// ------------------------------------------------------------------ 공유 승차권
// 목록의 예약 카드를 누르면 그 카드가 커지며 상세 화면의 승차권이 됨 (view-transition-name).
// 이름은 한 화면에 하나만 있어야 하므로, 누른 카드 하나에만 붙인다.

let sharedTicket: string | null = null
const listeners = new Set<() => void>()

export function setSharedTicket(id: string | null): void {
  if (sharedTicket === id) return
  sharedTicket = id
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 이 예약 카드가 화면 전환에서 상세 승차권과 이어지는지 */
export function useIsSharedTicket(id: string): boolean {
  return useSyncExternalStore(subscribe, () => sharedTicket === id)
}

/** 화면 전환에서 이어지는 승차권 (상세·새 예약 승차권과 누른 목록 카드) */
export const SHARED_TICKET_CLASS = 'kb-vt-ticket'
