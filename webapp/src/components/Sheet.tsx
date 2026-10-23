import { useEffect, useId, type ReactNode } from 'react'

function useEscape(onClose: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])
}

/** 하단 시트 (바깥을 누르거나 Esc로 닫힘) */
export function Sheet({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  useEscape(onClose)
  return (
    <div className="kb-sheet-backdrop" onClick={onClose}>
      <div
        className="kb-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-sheet__handle" />
        <h2 className="kb-sheet__title" id={titleId}>
          {title}
        </h2>
        {children}
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
