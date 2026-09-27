import { Link } from 'react-router-dom'
import type { Reservation } from '../api'
import { formatDate, relativeTime, timeRange } from '../format'
import { StatusBadge } from './StatusBadge'

export function ReservationCard({
  reservation: r,
  showOwner,
}: {
  reservation: Reservation
  showOwner?: boolean
}) {
  return (
    <Link to={`/r/${r.id}`} className={`card reservation-card status-${r.status}`}>
      <div className="card-row">
        <span className="route">
          {r.src_station}
          <span className="route-arrow">→</span>
          {r.dst_station}
        </span>
        <StatusBadge status={r.status} />
      </div>
      <div className="card-meta">
        <span>{formatDate(r.dep_date)}</span>
        <span>{timeRange(r)}</span>
      </div>
      <div className="card-sub">
        <span>
          {r.train_type_label} · {r.seat_type_label}
        </span>
        <span>
          {!r.is_active
            ? relativeTime(r.finished_at ?? r.updated_at)
            : r.attempts > 0
              ? `${r.attempts.toLocaleString()}회 시도`
              : r.status === 'running'
                ? '검색 시작'
                : '시작 대기'}
        </span>
      </div>
      {showOwner && (
        <div className="card-owner">
          {r.owner_id === 'admin' ? '관리자' : r.owner_id.replace(/(\d{3})(\d{4})(\d{4})/, '$1-$2-$3')}
          {' · '}
          {r.origin === 'web' ? '웹' : '텔레그램'}
        </div>
      )}
    </Link>
  )
}
