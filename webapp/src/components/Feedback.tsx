import type { ReactNode } from 'react'

export function Spinner({ label = '불러오는 중' }: { label?: string }) {
  return (
    <div className="kb-spinner-wrap" role="status" aria-label={label}>
      <span className="kb-spinner" />
    </div>
  )
}

/** 불러오는 동안 보여 줄 승차권 모양 (빙글 도는 표시 대신) */
export function TicketSkeleton({ count = 1, label = '불러오는 중' }: { count?: number; label?: string }) {
  return (
    <div className="kb-list" role="status" aria-label={label}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="kb-ticket kb-skeleton" aria-hidden="true">
          <div className="kb-ticket__paper">
            <div className="kb-ticket__main">
              <span className="kb-skeleton__line kb-skeleton__line--short" />
              <span className="kb-skeleton__line kb-skeleton__line--title" />
              <span className="kb-skeleton__line" />
            </div>
            <div className="kb-ticket__stub">
              <span className="kb-skeleton__line kb-skeleton__line--short" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="kb-error" role="alert">
      {message}
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="kb-empty">
      <strong>{title}</strong>
      {children}
    </div>
  )
}
