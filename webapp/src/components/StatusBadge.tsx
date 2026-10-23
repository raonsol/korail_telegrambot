import type { Reservation, ReservationStatus } from '../api'
import { STATUS_LABELS } from '../format'

export function StatusBadge({
  status,
  waitlisted = false,
}: {
  status: ReservationStatus
  waitlisted?: boolean
}) {
  const isWaitlisted = status === 'success' && waitlisted
  return (
    <span className={`kb-badge kb-badge--${isWaitlisted ? 'waitlisted' : status}`}>
      {(status === 'running' || status === 'queued') && <span className="kb-pulse" />}
      {isWaitlisted ? '예약대기 신청' : STATUS_LABELS[status]}
    </span>
  )
}

/** 열차 종류 태그 (KTX는 브랜드 색) */
export function TrainTag({ reservation: r, label }: { reservation: Pick<Reservation, 'train_type' | 'train_type_label'>; label?: string }) {
  return (
    <span className={`kb-tag${r.train_type === 'KTX' ? ' kb-tag--brand' : ''}`}>
      {label ?? r.train_type_label}
    </span>
  )
}
