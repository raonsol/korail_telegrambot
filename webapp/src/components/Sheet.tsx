import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react'
import { reducedMotion } from '../motion'

/** 닫힘 애니메이션 길이 (app.css --dur-sheet-out, .kb-dialog 닫힘과 맞춤) */
const SHEET_EXIT_MS = 240
const DIALOG_EXIT_MS = 160

function useEscape(onClose: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, enabled])
}

/**
 * 열림/닫힘 + 닫힘 애니메이션 동안 남아 있기.
 * present: 그려야 함 · closing: 닫히는 중 (exitMs 뒤에 사라짐)
 */
function usePresence(open: boolean, exitMs: number) {
  const [present, setPresent] = useState(open)
  if (open && !present) setPresent(true)
  useEffect(() => {
    if (open || !present) return
    const timer = window.setTimeout(() => setPresent(false), reducedMotion() ? 0 : exitMs)
    return () => window.clearTimeout(timer)
  }, [open, present, exitMs])
  return { present: open || present, closing: !open && present }
}

/** 열려 있는 동안의 마지막 값 (닫히는 애니메이션 동안 내용이 비지 않게) */
export function useLastWhileOpen<T>(open: boolean, value: T): T {
  const ref = useRef(value)
  if (open) ref.current = value
  return ref.current
}

/**
 * 하단 시트 (바깥을 누르거나 Esc, 아래로 끌어내려 닫힘).
 * 내용만 스크롤되고 footer(완료 버튼 등)는 항상 보임.
 * open 이 false 가 되면 내려가는 애니메이션 뒤에 사라짐 - 그동안은 마지막 내용을 보여 줌
 */
export function Sheet({
  open = true,
  title,
  onClose,
  footer,
  fill,
  children,
}: {
  open?: boolean
  title: string
  onClose: () => void
  footer?: ReactNode
  /** 내용과 상관없이 높이 고정 (안쪽 목록이 남는 높이를 채움 - 결과가 바뀌어도 시트 크기가 그대로) */
  fill?: boolean
  children: ReactNode
}) {
  const titleId = useId()
  const { present, closing } = usePresence(open, SHEET_EXIT_MS)
  const shown = useLastWhileOpen(open, { title, footer, fill, children })
  const sheetRef = useRef<HTMLDivElement>(null)
  const backdropRef = useRef<HTMLDivElement>(null)
  useEscape(onClose, open)
  useSheetDrag(sheetRef, backdropRef, open ? onClose : null, present)

  if (!present) return null
  return (
    <div
      ref={backdropRef}
      className={`kb-sheet-backdrop${closing ? ' is-closing' : ''}`}
      onClick={open ? onClose : undefined}
    >
      <div
        ref={sheetRef}
        className={`kb-sheet${shown.fill ? ' kb-sheet--fill' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-sheet__handle" />
        <h2 className="kb-sheet__title" id={titleId}>
          {shown.title}
        </h2>
        <div className="kb-sheet__content">{shown.children}</div>
        {shown.footer && <div className="kb-sheet__footer">{shown.footer}</div>}
      </div>
    </div>
  )
}

/** 이만큼 끌어내리거나 (시트 높이의 비율, 최대 px) */
const DISMISS_RATIO = 0.3
const DISMISS_MAX = 140
/** 이 속도(px/ms) 이상으로 튕기면 닫힘 */
const DISMISS_VELOCITY = 0.5
/** 끌기 대상이 아닌 곳 (슬라이더, 입력칸) */
const NO_DRAG = '.kb-timebar, input, textarea, select, [data-no-sheet-drag]'

/**
 * 시트를 손가락으로 끌어내려 닫기. 내용이 맨 위로 스크롤돼 있을 때만 (아니면 스크롤이 우선).
 * 끄는 동안은 시트와 뒤 배경을 직접 움직이고, 놓으면 닫거나 제자리로 돌아감
 */
function useSheetDrag(
  sheetRef: RefObject<HTMLDivElement | null>,
  backdropRef: RefObject<HTMLDivElement | null>,
  onClose: (() => void) | null,
  present: boolean,
) {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const sheet = sheetRef.current
    const backdrop = backdropRef.current
    if (!sheet || !backdrop) return

    let start: { x: number; y: number } | null = null
    let dragging = false
    let offset = 0
    let samples: { y: number; t: number }[] = []
    let settleTimer = 0

    const move = (y: number) => {
      offset = y
      sheet.style.transform = `translateY(${y}px)`
      backdrop.style.setProperty('--scrim-opacity', String(Math.max(0, 1 - y / sheet.offsetHeight)))
    }
    const settle = () => {
      sheet.style.transition = 'transform var(--dur-sheet) var(--ease-nav)'
      sheet.style.transform = ''
      backdrop.style.removeProperty('--scrim-opacity')
      window.clearTimeout(settleTimer)
      settleTimer = window.setTimeout(() => (sheet.style.transition = ''), 400)
    }

    const onStart = (e: TouchEvent) => {
      start = null
      if (e.touches.length !== 1 || !onCloseRef.current) return
      const target = e.target as Element
      if (target.closest(NO_DRAG)) return
      // 안쪽 스크롤 영역이 위로 올라가 있으면 스크롤이 먼저
      for (let el: Element | null = target; el && el !== sheet; el = el.parentElement) {
        if (el.scrollTop > 0) return
      }
      const touch = e.touches[0]
      start = { x: touch.clientX, y: touch.clientY }
      samples = [{ y: touch.clientY, t: e.timeStamp }]
    }

    const onMove = (e: TouchEvent) => {
      if (!start) return
      const touch = e.touches[0]
      const dy = touch.clientY - start.y
      const dx = touch.clientX - start.x
      if (!dragging) {
        // 첫 움직임으로 결정: 아래로가 주된 방향이면 시트 끌기, 아니면 기본 동작(스크롤)에 맡김
        if (dy <= 0 || Math.abs(dx) > dy) {
          start = null
          return
        }
        dragging = true
        window.clearTimeout(settleTimer)
        sheet.style.transition = 'none'
      }
      e.preventDefault()
      move(Math.max(0, dy))
      samples.push({ y: touch.clientY, t: e.timeStamp })
      if (samples.length > 6) samples.shift()
    }

    const onEnd = () => {
      start = null
      if (!dragging) return
      dragging = false
      const first = samples[0]
      const last = samples[samples.length - 1]
      const velocity = last.t > first.t ? (last.y - first.y) / (last.t - first.t) : 0
      const threshold = Math.min(DISMISS_MAX, sheet.offsetHeight * DISMISS_RATIO)
      if (onCloseRef.current && (offset > threshold || velocity > DISMISS_VELOCITY)) {
        // 지금 위치에서 닫힘 애니메이션(.is-closing)이 이어짐
        onCloseRef.current()
      } else {
        settle()
      }
    }

    sheet.addEventListener('touchstart', onStart, { passive: true })
    sheet.addEventListener('touchmove', onMove, { passive: false })
    sheet.addEventListener('touchend', onEnd)
    sheet.addEventListener('touchcancel', onEnd)
    return () => {
      window.clearTimeout(settleTimer)
      sheet.removeEventListener('touchstart', onStart)
      sheet.removeEventListener('touchmove', onMove)
      sheet.removeEventListener('touchend', onEnd)
      sheet.removeEventListener('touchcancel', onEnd)
    }
  }, [sheetRef, backdropRef, present])
}

/** 가운데 안내 팝업. open 이 false 가 되면 작아지며 사라짐 */
export function Dialog({
  open = true,
  title,
  icon,
  onClose,
  children,
}: {
  open?: boolean
  title: string
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const { present, closing } = usePresence(open, DIALOG_EXIT_MS)
  const shown = useLastWhileOpen(open, { title, icon, children })
  useEscape(onClose, open)
  if (!present) return null
  return (
    <div
      className={`kb-dialog-backdrop${closing ? ' is-closing' : ''}`}
      onClick={open ? onClose : undefined}
    >
      <div
        className="kb-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-dialog__head">
          {shown.icon && <span className="kb-dialog__icon">{shown.icon}</span>}
          <h2 id={titleId}>{shown.title}</h2>
        </div>
        {shown.children}
      </div>
    </div>
  )
}
