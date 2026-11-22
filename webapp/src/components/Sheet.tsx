import { useEffect, useId, type ReactNode } from 'react'

function useEscape(onClose: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])
}

/** 하단 시트 (바깥을 누르거나 Esc로 닫힘). 내용만 스크롤되고 footer(완료 버튼 등)는 항상 보임 */
export function Sheet({
  title,
  onClose,
  footer,
  fill,
  children,
}: {
  title: string
  onClose: () => void
  footer?: ReactNode
  /** 내용과 상관없이 높이 고정 (안쪽 목록이 남는 높이를 채움 - 결과가 바뀌어도 시트 크기가 그대로) */
  fill?: boolean
  children: ReactNode
}) {
  const titleId = useId()
  useEscape(onClose)
  return (
    <div className="kb-sheet-backdrop" onClick={onClose}>
      <div
        className={`kb-sheet${fill ? ' kb-sheet--fill' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-sheet__handle" />
        <h2 className="kb-sheet__title" id={titleId}>
          {title}
        </h2>
        <div className="kb-sheet__content">{children}</div>
        {footer && <div className="kb-sheet__footer">{footer}</div>}
      </div>
    </div>
  )
}

/** 가운데 안내 팝업 */
export function Dialog({
  title,
  icon,
  onClose,
  children,
}: {
  title: string
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  useEscape(onClose)
  return (
    <div className="kb-dialog-backdrop" onClick={onClose}>
      <div
        className="kb-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-dialog__head">
          {icon && <span className="kb-dialog__icon">{icon}</span>}
          <h2 id={titleId}>{title}</h2>
        </div>
        {children}
      </div>
    </div>
  )
}
