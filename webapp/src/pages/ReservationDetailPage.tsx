import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Api, type Reservation } from '../api'
import { useAuth } from '../auth'
import { ErrorBox, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { ticketTone } from '../components/ReservationCard'
import { StatusBadge } from '../components/StatusBadge'
import { RouteLine, Ticket } from '../components/Ticket'
import {
  KORAIL_PAYMENT_URL,
  SEAT_SUMMARY,
  formatDate,
  formatDateTime,
  parseResultText,
  relativeTime,
  timeRange,
  type ResultInfo,
} from '../format'
import { getPushState, subscribePush, type PushState } from '../push'

export function ReservationDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['reservation', id],
    queryFn: () => Api.reservation(id),
    refetchInterval: (q) => (q.state.data?.is_active ? 15_000 : false),
  })

  const cancel = useMutation({
    mutationFn: () => Api.cancelReservation(id),
    onSuccess: (reservation) => {
      queryClient.setQueryData(['reservation', id], reservation)
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
    },
  })

  const r = query.data

  return (
    <Layout title="예약 상세" back>
      {query.isPending ? (
        <Spinner />
      ) : query.isError || !r ? (
        <ErrorBox error={query.error ?? '예약을 찾을 수 없습니다.'} />
      ) : (
        <div className="kb-stack">
          <DetailTicket reservation={r} info={r.status === 'success' ? parseResultText(r.result_text) : {}} />

          {r.is_active && <PushPrompt />}

          {r.status === 'success' && r.waitlisted && (
            <section className="kb-card kb-card--success kb-result-card">
              <h2>예약대기를 신청했습니다</h2>
              {r.result_text && <p className="kb-result-text kb-num">{r.result_text}</p>}
              <p className="kb-small">
                <strong>아직 좌석이 확보된 것은 아닙니다.</strong> 좌석이 배정되면 코레일이
                알려주며, 안내받은 기한 안에 결제해야 합니다.
              </p>
              <p className="kb-hint">
                신청 내역 확인/취소: 코레일톡 앱 오른쪽 상단 메뉴 → 승차권 예매 → 예약 승차권
                조회/취소
              </p>
            </section>
          )}

          {r.status === 'success' && !r.waitlisted && (
            <section className="kb-card kb-card--success kb-result-card">
              <h2>예약에 성공했습니다</h2>
              {r.result_text && <p className="kb-result-text kb-num">{r.result_text}</p>}
              <p className="kb-small">
                <strong>20분 안에 결제하지 않으면 예약이 취소됩니다.</strong>
              </p>
              <a
                className="kb-btn kb-btn--primary kb-btn--block"
                href={KORAIL_PAYMENT_URL}
                target="_blank"
                rel="noreferrer"
              >
                코레일 홈페이지에서 결제하기
              </a>
              <p className="kb-hint">
                코레일톡 앱: 오른쪽 상단 메뉴 → 승차권 예매 → 예약 승차권 조회/취소
              </p>
            </section>
          )}

          {(r.status === 'failed' || r.status === 'error') && r.error && (
            <div className="kb-error" role="alert">
              {r.error}
            </div>
          )}

          <section className="kb-card">
            <dl className="kb-summary">
              <dt>열차</dt>
              <dd>{r.train_type_label}</dd>
              <dt>좌석</dt>
              <dd>{SEAT_SUMMARY[r.seat_type]}</dd>
              <dt>예약대기</dt>
              <dd>{r.allow_waitlist ? '사용' : '사용 안 함'}</dd>
              <dt>시작</dt>
              <dd>
                {formatDateTime(r.created_at)} ({r.origin === 'web' ? '웹' : '텔레그램'})
              </dd>
              {r.finished_at && (
                <>
                  <dt>종료</dt>
                  <dd>{formatDateTime(r.finished_at)}</dd>
                </>
              )}
            </dl>
          </section>

          <ErrorBox error={cancel.error} />

          {r.is_active ? (
            <button
              className="kb-btn kb-btn--danger kb-btn--block kb-detail-action"
              disabled={cancel.isPending}
              onClick={() => {
                if (window.confirm('이 예약 작업을 취소할까요?')) cancel.mutate()
              }}
            >
              {cancel.isPending ? '취소하는 중…' : '예약 작업 취소'}
            </button>
          ) : (
            <button
              className="kb-btn kb-btn--block kb-detail-action"
              onClick={() => navigate('/new', { state: { prefill: r } })}
            >
              <Icon name="refresh" size={18} /> 같은 조건으로 다시 예약
            </button>
          )}
        </div>
      )}
    </Layout>
  )
}

/** 상태에 따라 용지 색이 바뀌는 상단 승차권 */
function DetailTicket({ reservation: r, info }: { reservation: Reservation; info: ResultInfo }) {
  const stubRight =
    r.status === 'queued'
      ? '시작 대기'
      : r.is_active
        ? `${relativeTime(r.updated_at)} 갱신`
        : r.status === 'success' && r.waitlisted
          ? '예약대기 신청'
          : r.status === 'success' && info.payBy
            ? `결제 기한 ${info.payBy}`
            : `${formatDateTime(r.finished_at ?? r.updated_at)} 종료`

  return (
    <Ticket
      tone={ticketTone(r, 'brand')}
      stub={
        <>
          <span>
            시도 <b className="kb-num">{r.attempts.toLocaleString()}회</b>
          </span>
          <span>{stubRight}</span>
        </>
      }
    >
      <div className="kb-ticket__head">
        <span className="kb-caption" style={{ color: 'var(--text-2)' }}>
          {formatDate(r.dep_date)} · {info.trainNo ?? r.train_type_label}
        </span>
        <StatusBadge status={r.status} waitlisted={r.waitlisted} />
      </div>
      <RouteLine
        size="lg"
        from={r.src_station}
        to={r.dst_station}
        fromTime={info.depTime}
        toTime={info.arrTime}
        status={r.status}
      />
      <div className="kb-ticket__window--detail">
        <span className="kb-caption">{info.fare ? '운임' : '출발 시각 범위'}</span>
        <span className="kb-num">{info.fare ?? timeRange(r)}</span>
      </div>
    </Ticket>
  )
}

/** 진행 중인 예약에서 알림 권한 요청 (맥락 없이 앱 진입 시 요청하지 않음) */
function PushPrompt() {
  const { me } = useAuth()
  const [state, setState] = useState<PushState | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getPushState().then(setState)
  }, [])

  if (!me?.push_enabled || !me.push_public_key || state !== 'unsubscribed') return null

  return (
    <div className="kb-card kb-notice">
      <Icon name="bell" />
      <div>
        <strong>예약 결과를 알림으로 받으세요</strong>
        <p className="kb-hint">앱을 닫아도 예약 성공/실패를 바로 알려드립니다.</p>
        <ErrorBox error={error} />
      </div>
      <button
        className="kb-btn kb-btn--sm kb-btn--primary"
        onClick={async () => {
          try {
            setState(await subscribePush(me.push_public_key!))
          } catch (e) {
            setError(e)
          }
        }}
      >
        알림 켜기
      </button>
    </div>
  )
}
