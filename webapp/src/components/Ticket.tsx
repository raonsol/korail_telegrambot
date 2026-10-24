import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export type TicketTone = 'plain' | 'brand' | 'success' | 'muted'

/** 승차권 — 본문과 꼬리표(stub) 사이에 펀치홀 + 절취선 */
export function Ticket({
  tone = 'plain',
  to,
  stub,
  className,
  children,
}: {
  tone?: TicketTone
  to?: string
  stub?: ReactNode
  className?: string
  children: ReactNode
}) {
  const cls = ['kb-ticket', tone !== 'plain' && `kb-ticket--${tone}`, className]
    .filter(Boolean)
    .join(' ')
  const paper = (
    <div className="kb-ticket__paper">
      <div className="kb-ticket__main">{children}</div>
      {stub && <div className="kb-ticket__stub">{stub}</div>}
    </div>
  )
  return to ? (
    <Link to={to} className={cls}>
      {paper}
    </Link>
  ) : (
    <div className={cls}>{paper}</div>
  )
}

/** 노선도: 출발(빈 점) → 선 → 도착(찬 점). 상태에 따라 색/점선/움직이는 열차 */
export function RouteLine({
  from,
  to,
  fromTime,
  toTime,
  status,
  size,
}: {
  from: ReactNode
  to: ReactNode
  fromTime?: string
  toTime?: string
  status: string
  size?: 'sm' | 'lg'
}) {
  return (
    <div className={`kb-route${size ? ` kb-route--${size}` : ''}`} data-status={status}>
      <div className="kb-route__stop">
        {from}
        {fromTime && <small>{fromTime}</small>}
      </div>
      <div className="kb-route__track" aria-hidden="true">
        <span className="kb-route__train" />
      </div>
      <div className="kb-route__stop kb-route__stop--end">
        {to}
        {toTime && <small>{toTime}</small>}
      </div>
    </div>
  )
}
