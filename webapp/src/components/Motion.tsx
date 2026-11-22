import { useEffect, useRef, useState, type ReactNode } from 'react'
import { reducedMotion } from '../motion'

/** 목록 항목이 접히며 사라지는 시간 (app.css .kb-list-item.is-leaving 과 맞춤) */
const LEAVE_MS = 260

type Flash = 'success' | 'fail' | null

/**
 * 보고 있는 동안 예약이 끝나면 한 번 반응 (성공: 도장 찍히듯, 실패: 가볍게 흔들림).
 * 처음 그릴 때 이미 끝나 있던 예약에는 반응하지 않음
 */
export function useStatusFlash(status: string): Flash {
  const previous = useRef(status)
  const [flash, setFlash] = useState<Flash>(null)
  useEffect(() => {
    if (previous.current === status) return
    previous.current = status
    const next: Flash =
      status === 'success' ? 'success' : status === 'failed' || status === 'error' ? 'fail' : null
    if (!next) return
    setFlash(next)
    const timer = window.setTimeout(() => setFlash(null), 900)
    return () => window.clearTimeout(timer)
  }, [status])
  return flash
}

export function flashClass(flash: Flash): string | undefined {
  return flash ? `kb-flash--${flash}` : undefined
}

/** 바뀔 때마다 아래에서 올라오는 숫자 (시도 횟수). 처음 값은 그대로 */
export function TickNumber({ value, children }: { value: number; children: ReactNode }) {
  const first = useRef(value)
  return (
    <span key={value} className={value !== first.current ? 'kb-tick' : undefined}>
      {children}
    </span>
  )
}

interface Entry<T> {
  key: string
  item: T
  phase: 'in' | 'stay' | 'out'
}

function merge<T>(prev: Entry<T>[], items: T[], getKey: (item: T) => string, animateIn: boolean) {
  const keys = new Set(items.map(getKey))
  const next: Entry<T>[] = items.map((item) => {
    const key = getKey(item)
    const stays = prev.some((e) => e.key === key && e.phase !== 'out')
    return { key, item, phase: stays || !animateIn ? 'stay' : 'in' }
  })
  // 빠진 항목은 원래 자리에서 접히며 사라짐
  prev.forEach((entry, index) => {
    if (!keys.has(entry.key)) next.splice(Math.min(index, next.length), 0, { ...entry, phase: 'out' })
  })
  return next
}

/**
 * 항목이 생기면 펼쳐지며 나타나고, 빠지면 접히며 사라지는 목록 (.kb-list).
 * 처음 그릴 때는 애니메이션 없음
 */
export function AnimatedList<T>({
  items,
  getKey,
  render,
}: {
  items: T[]
  getKey: (item: T) => string
  render: (item: T) => ReactNode
}) {
  const [entries, setEntries] = useState(() => merge<T>([], items, getKey, false))
  const [seen, setSeen] = useState(items)
  if (items !== seen) {
    setSeen(items)
    setEntries((prev) => merge(prev, items, getKey, !reducedMotion()))
  }

  // 접힌 항목 제거 (애니메이션은 forwards 라 늦게 지워져도 보이지 않음)
  const leaving = entries
    .filter((e) => e.phase === 'out')
    .map((e) => e.key)
    .join(' ')
  useEffect(() => {
    if (!leaving) return
    const keys = new Set(leaving.split(' '))
    const timer = window.setTimeout(
      () => setEntries((prev) => prev.filter((e) => !(e.phase === 'out' && keys.has(e.key)))),
      LEAVE_MS,
    )
    return () => window.clearTimeout(timer)
  }, [leaving])

  if (entries.length === 0) return null
  return (
    <div className="kb-list">
      {entries.map((e) => (
        <div
          key={e.key}
          className={`kb-list-item${e.phase === 'in' ? ' is-entering' : ''}${e.phase === 'out' ? ' is-leaving' : ''}`}
          aria-hidden={e.phase === 'out' || undefined}
        >
          <div className="kb-list-item__inner">{render(e.item)}</div>
        </div>
      ))}
    </div>
  )
}
