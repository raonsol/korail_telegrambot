import type { ReservationStatus } from '../api'
import { STATUS_LABELS } from '../format'

export function StatusBadge({
  status,
  waitlisted = false,
}: {
  status: ReservationStatus
  waitlisted?: boolean
}) {
  return (
    <span className={`badge badge-${status}`}>
      {(status === 'running' || status === 'queued') && <span className="pulse" />}
      {status === 'success' && waitlisted ? '예약대기 신청' : STATUS_LABELS[status]}
    </span>
  )
}
