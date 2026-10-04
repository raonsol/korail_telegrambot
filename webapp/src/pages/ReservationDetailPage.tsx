import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Api } from '../api'
import { useAuth } from '../auth'
import { ErrorBox, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { StatusBadge } from '../components/StatusBadge'
import { KORAIL_PAYMENT_URL, formatDate, formatDateTime, timeRange } from '../format'
import { getPushState, subscribePush, type PushState } from '../push'

export function ReservationDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const justCreated = Boolean((location.state as { created?: boolean } | null)?.created)

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
        <>
          <section className={`hero status-${r.status}`}>
            <StatusBadge status={r.status} waitlisted={r.waitlisted} />
            <div className="hero-route">
              <span>{r.src_station}</span>
              <Icon name="arrow" size={22} />
              <span>{r.dst_station}</span>
            </div>
            <div className="hero-meta">
              {formatDate(r.dep_date)} · {timeRange(r)}
            </div>
            {r.is_active && (
              <p className="hero-note">
                {r.status === 'queued'
                  ? '예약 작업이 곧 시작됩니다.'
                  : `빈 좌석을 찾고 있습니다 · ${r.attempts.toLocaleString()}회 시도`}
              </p>
            )}
          </section>

          {justCreated && r.is_active && <PushPrompt />}

          {r.status === 'success' && r.waitlisted && (
            <section className="card success-card">
              <h2>예약대기를 신청했습니다</h2>
              {r.result_text && <p className="result-text">{r.result_text}</p>}
              <p>
                <strong>아직 좌석이 확보된 것은 아닙니다.</strong> 좌석이 배정되면 코레일이
                알려주며, 안내받은 기한 안에 결제해야 합니다.
              </p>
              <p className="muted small">
                신청 내역 확인/취소: 코레일톡 앱 오른쪽 상단 메뉴 → 승차권 예매 → 예약 승차권
                조회/취소
              </p>
            </section>
          )}

          {r.status === 'success' && !r.waitlisted && (
            <section className="card success-card">
              <h2>예약에 성공했습니다 🎉</h2>
              {r.result_text && <p className="result-text">{r.result_text}</p>}
              <p>
                <strong>20분 안에 결제하지 않으면 예약이 취소됩니다.</strong>
              </p>
              <a className="button primary block" href={KORAIL_PAYMENT_URL} target="_blank" rel="noreferrer">
                코레일 홈페이지에서 결제하기
              </a>
              <p className="muted small">
                코레일톡 앱: 오른쪽 상단 메뉴 → 승차권 예매 → 예약 승차권 조회/취소
              </p>
            </section>
          )}

          {(r.status === 'failed' || r.status === 'error') && r.error && (
            <div className="error-box" role="alert">
              {r.error}
            </div>
          )}

          <section className="card">
            <dl className="summary">
              <dt>열차</dt>
              <dd>{r.train_type_label}</dd>
              <dt>좌석</dt>
              <dd>{r.seat_type_label}</dd>
              <dt>시도 횟수</dt>
              <dd>{r.attempts.toLocaleString()}회</dd>
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

          <div className="actions">
            {r.is_active ? (
              <button
                className="button danger block"
                disabled={cancel.isPending}
                onClick={() => {
                  if (window.confirm('이 예약 작업을 취소할까요?')) cancel.mutate()
                }}
              >
                {cancel.isPending ? '취소하는 중…' : '예약 작업 취소'}
              </button>
            ) : (
              <button
                className="button block"
                onClick={() => navigate('/new', { state: { prefill: r } })}
              >
                <Icon name="refresh" size={18} /> 같은 조건으로 다시 예약
              </button>
            )}
          </div>
        </>
      )}
    </Layout>
  )
}

/** 첫 예약 직후 알림 권한 요청 (맥락 없이 앱 진입 시 요청하지 않음) */
function PushPrompt() {
  const { me } = useAuth()
  const [state, setState] = useState<PushState | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getPushState().then(setState)
  }, [])

  if (!me?.push_enabled || !me.push_public_key || state !== 'unsubscribed') return null

  return (
    <div className="card notice">
      <Icon name="bell" />
      <div>
        <strong>예약 결과를 알림으로 받으세요</strong>
        <p className="muted small">앱을 닫아도 예약 성공/실패를 바로 알려드립니다.</p>
        <ErrorBox error={error} />
      </div>
      <button
        className="button small primary"
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
