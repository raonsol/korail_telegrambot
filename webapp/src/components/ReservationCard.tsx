import type { Reservation } from '../api'
import { SEAT_SUMMARY, displayStatus, formatDate, relativeTime, timeRange } from '../format'
import { StatusBadge, TrainTag } from './StatusBadge'
import { RouteLine, Ticket, type TicketTone } from './Ticket'

export function ticketTone(r: Reservation, activeTone: TicketTone = 'plain'): TicketTone {
  if (r.is_active) return activeTone
  // 코레일에서 취소한 예약은 상태가 success 여도 취소 색
  return displayStatus(r) === 'success' ? 'success' : 'muted'
}

export function ReservationCard({ reservation: r }: { reservation: Reservation }) {
  return (
    <Ticket
      to={`/r/${r.id}`}
      tone={ticketTone(r)}
      stub={
        <>
          <span>{SEAT_SUMMARY[r.seat_type]}</span>
          <span className={r.is_active ? 'kb-num' : undefined}>
            {!r.is_active
              ? relativeTime(r.finished_at ?? r.updated_at)
              : r.attempts > 0
                ? `${r.attempts.toLocaleString()}회 시도`
                : r.status === 'running'
                  ? '검색 시작'
                  : '시작 대기'}
          </span>
        </>
      }
    >
      <div className="kb-ticket__head">
        <TrainTag reservation={r} />
        <StatusBadge
          status={r.status}
          waitlisted={r.waitlisted}
          korailCancelled={Boolean(r.korail_cancelled_at)}
        />
      </div>
      <RouteLine from={r.src_station} to={r.dst_station} status={displayStatus(r)} />
      <div className="kb-ticket__window">
        {formatDate(r.dep_date)} · <span className="kb-num">{timeRange(r)}</span>
      </div>
    </Ticket>
  )
}
