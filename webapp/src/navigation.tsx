import { useCallback, useEffect, useLayoutEffect, type MouseEvent } from 'react'
import {
  Link,
  useLocation,
  useNavigate,
  useNavigationType,
  type LinkProps,
  type To,
} from 'react-router-dom'
import { routeCommitted, runNavTransition, setSharedTicket, type NavDirection } from './motion'

interface GoOptions {
  direction?: NavDirection
  replace?: boolean
  state?: unknown
  /** 이 예약 카드를 상세 승차권으로 이어지게 (목록 → 상세) */
  sharedTicket?: string
}

/** navigate + 화면 전환 애니메이션. 숫자(-1)는 뒤로 가기 */
export function useTransitionNavigate() {
  const navigate = useNavigate()
  const location = useLocation()
  const here = location.pathname + location.search

  return useCallback(
    (to: To | number, { direction = 'push', replace, state, sharedTicket }: GoOptions = {}) => {
      const go = () =>
        typeof to === 'number' ? navigate(to) : navigate(to, { replace, state })
      // 지금 화면으로 다시 이동 (활성 탭을 또 누름 등) - 애니메이션 없이
      if (typeof to === 'string' && to === here) {
        go()
        return
      }
      if (direction !== 'pop') setSharedTicket(sharedTicket ?? null)
      runNavTransition(direction, go)
    },
    [navigate, here],
  )
}

function isPlainLeftClick(e: MouseEvent<HTMLAnchorElement>) {
  return e.button === 0 && !e.metaKey && !e.altKey && !e.ctrlKey && !e.shiftKey
}

/** 화면 전환 애니메이션과 함께 이동하는 링크 (새 탭 열기 등 수정키 클릭은 그대로) */
export function TransitionLink({
  direction = 'push',
  sharedTicket,
  onClick,
  ...props
}: LinkProps & { direction?: NavDirection; sharedTicket?: string }) {
  const go = useTransitionNavigate()
  return (
    <Link
      {...props}
      onClick={(e) => {
        onClick?.(e)
        if (e.defaultPrevented || !isPlainLeftClick(e) || props.target || props.reloadDocument) {
          return
        }
        e.preventDefault()
        go(props.to, { direction, replace: props.replace, state: props.state, sharedTicket })
      }}
    />
  )
}

/** 화면별 스크롤 위치 (location.key -> scrollY). 새로고침하면 사라져도 됨 */
const scrollPositions = new Map<string, number>()

/**
 * 화면이 바뀐 직후: 앞으로 가면 맨 위, 뒤로 가면 떠날 때의 스크롤 위치로.
 * 그다음 화면 전환에 새 화면이 그려졌다고 알림 (전환 애니메이션이 이 상태를 찍음)
 */
export function RouteTransitions() {
  const location = useLocation()
  const navigationType = useNavigationType()

  useEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual'
  }, [])

  // 지금 화면의 스크롤 위치를 계속 기록 (떠날 때는 이미 새 화면이 그려진 뒤라 읽을 수 없음)
  useEffect(() => {
    const key = location.key
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => scrollPositions.set(key, window.scrollY))
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
    }
  }, [location.key])

  useLayoutEffect(() => {
    const y = navigationType === 'POP' ? (scrollPositions.get(location.key) ?? 0) : 0
    window.scrollTo(0, y)
    routeCommitted()
  }, [location.key, navigationType])

  return null
}
