import type { ReactNode } from 'react'

export function Spinner({ label = '불러오는 중' }: { label?: string }) {
  return (
    <div className="kb-spinner-wrap" role="status" aria-label={label}>
      <span className="kb-spinner" />
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
