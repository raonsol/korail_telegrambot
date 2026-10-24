import type { ReservationStatus } from '../api'
import { STATUS_LABELS } from '../format'

export function StatusBadge({
  status,
  waitlisted = false,
  korailCancelled = false,
}: {
  status: ReservationStatus
  waitlisted?: boolean
  /** 성공한 예약을 코레일에서도 취소함 */
  korailCancelled?: boolean
}) {
  if (status === 'success' && korailCancelled) {
    return <span className="badge badge-cancelled">코레일 취소</span>
  }
  return (
    <span className={`badge badge-${status}`}>
      {(status === 'running' || status === 'queued') && <span className="pulse" />}
      {status === 'success' && waitlisted ? '예약대기 신청' : STATUS_LABELS[status]}
    </span>
  )
}
