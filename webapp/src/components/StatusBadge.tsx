import type { ReservationStatus } from '../api'
import { STATUS_LABELS } from '../format'

export function StatusBadge({ status }: { status: ReservationStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      {(status === 'running' || status === 'queued') && <span className="pulse" />}
      {STATUS_LABELS[status]}
    </span>
  )
}
