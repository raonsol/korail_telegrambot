import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Api, type KorailAccount, type KorailReservation, type KorailTrain } from '../api'
import { ErrorBox } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { Segmented } from '../components/Segmented'
import { Dialog } from '../components/Sheet'
import { RouteLine, Ticket } from '../components/Ticket'
import {
  KORAIL_PAYMENT_URL,
  formatClock,
  formatDate,
  formatTime,
  formatWon,
  timeLeft,
  trainNumber,
} from '../format'

type Tab = 'pending' | 'issued'

const QUERY_KEY = ['korail-account']

/** 내 코레일 예약·승차권: 코레일 계정에 지금 있는 결제 전 예약과 발권된 승차권 */
export function TicketsPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<Tab>('pending')
  const [target, setTarget] = useState<KorailReservation | null>(null)
  const now = useNow(30_000)

  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => Api.korailAccount(),
    // 코레일에 예약마다 상세 조회가 나가므로 자동으로 다시 묻지 않음 (새로고침 버튼으로만)
    staleTime: 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const refresh = useMutation({
    mutationFn: () => Api.korailAccount(true),
    onSuccess: (data) => queryClient.setQueryData(QUERY_KEY, data),
  })

  const cancel = useMutation({
    mutationFn: (rsvId: string) => Api.cancelKorailRsv(rsvId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      // 코레일봇이 잡은 예약이면 그 예약 기록도 "코레일 취소"로 바뀜
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
      queryClient.invalidateQueries({ queryKey: ['reservation'] })
      setTarget(null)
    },
  })

  /** 취소 확인 창 열기·닫기 (이전 취소의 오류가 다음 창에 남지 않도록) */
  const openCancel = (r: KorailReservation | null) => {
    cancel.reset()
    setTarget(r)
  }

  const data = query.data
  const loading = query.isPending || (refresh.isPending && !data)
  const error = refresh.error ?? query.error

  return (
    <Layout
      title="내 승차권"
      actions={
        <button
          className="kb-icon-btn"
          aria-label="새로고침"
          disabled={refresh.isPending || query.isFetching}
          onClick={() => refresh.mutate()}
        >
          <Icon name="refresh" size={20} />
        </button>
      }
    >
      <div className="kb-account">
        <span>{data ? <b>{data.korail_id}</b> : null} 코레일 계정</span>
        <span className="kb-num">
          {refresh.isPending || query.isFetching
            ? '불러오는 중'
            : data
              ? `${formatClock(data.fetched_at)} 조회`
              : error
                ? '조회 실패'
                : ''}
        </span>
      </div>

      <div className="kb-tickets-tabs">
        <Segmented
          name="승차권 종류"
          value={tab}
          onChange={setTab}
          options={[
            {
              value: 'pending',
              label: `결제 대기${data ? ` ${data.reservations.length}` : ''}`,
            },
            {
              value: 'issued',
              label: `발권 완료${data ? ` ${data.tickets.length}` : ''}`,
            },
          ]}
        />
      </div>

      <ErrorBox error={error} />

      {loading ? (
        <LoadingTicket />
      ) : !data ? (
        <div className="kb-empty">
          <p>코레일 예약·승차권을 불러오지 못했습니다.</p>
          <button className="kb-btn kb-btn--sm" onClick={() => refresh.mutate()}>
            다시 시도
          </button>
        </div>
      ) : tab === 'pending' ? (
        <PendingList account={data} now={now} onCancel={openCancel} />
      ) : (
        <IssuedList account={data} />
      )}

      {target && (
        <Dialog
          title={target.waiting ? '예약대기를 취소할까요?' : '코레일 예약을 취소할까요?'}
          icon={<Icon name="trash" size={18} />}
          onClose={() => !cancel.isPending && openCancel(null)}
        >
          <p>
            <b>
              {target.train_type} {trainNumber(target.train_no)}
            </b>{' '}
            · {formatDate(target.dep_date)} {formatTime(target.dep_time)}
            <br />
            {target.dep_station} → {target.arr_station} · {formatWon(target.price)} ·{' '}
            {target.seat_count}석
          </p>
          <p className="kb-hint">취소한 좌석은 되돌릴 수 없습니다.</p>
          <ErrorBox error={cancel.error} />
          <div className="kb-dialog__actions">
            <button className="kb-btn" disabled={cancel.isPending} onClick={() => openCancel(null)}>
              아니오
            </button>
            <button
              className="kb-btn kb-btn--danger"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(target.rsv_id)}
            >
              {cancel.isPending ? '취소하는 중…' : target.waiting ? '예약대기 취소' : '예약 취소'}
            </button>
          </div>
        </Dialog>
      )}
    </Layout>
  )
}

function PendingList({
  account,
  now,
  onCancel,
}: {
  account: KorailAccount
  now: number
  onCancel: (r: KorailReservation) => void
}) {
  if (account.reservations.length === 0) {
    return (
      <div className="kb-empty">
        <p>결제를 기다리는 코레일 예약이 없습니다.</p>
        <Link className="kb-btn kb-btn--sm kb-btn--primary" to="/new">
          새 예약 시작
        </Link>
      </div>
    )
  }
  return (
    <>
      <div className="kb-list">
        {account.reservations.map((r, i) => (
          <Ticket
            key={`${r.rsv_id}-${i}`}
            tone={r.waiting ? 'plain' : 'brand'}
            stub={
              <>
                <span>
                  예약번호 <b className="kb-num">{r.rsv_id}</b>
                </span>
                {r.waiting ? (
                  <span>좌석 배정 대기 중</span>
                ) : r.pay_deadline ? (
                  <span className="kb-deadline">
                    {formatClock(r.pay_deadline)}까지 · {timeLeft(r.pay_deadline, now)}
                  </span>
                ) : null}
              </>
            }
          >
            <div className="kb-ticket__head">
              <span>
                <TrainName item={r} />
                <Source item={r} />
              </span>
              <span className={`kb-badge kb-badge--${r.waiting ? 'waitlisted' : 'running'}`}>
                {r.waiting ? '예약대기' : '결제 대기'}
              </span>
            </div>
            <RouteLine
              size="lg"
              from={r.dep_station}
              to={r.arr_station}
              fromTime={formatTime(r.dep_time)}
              toTime={formatTime(r.arr_time)}
              status={r.waiting ? 'queued' : 'running'}
            />
            <div className="kb-ticket__window">
              {formatDate(r.dep_date)} · <span className="kb-num">{formatWon(r.price)}</span> ·{' '}
              {r.seat_count}석
            </div>
            <div className={`kb-ticket__actions${r.waiting ? ' is-single' : ''}`}>
              {!r.waiting && (
                <a
                  className="kb-btn kb-btn--primary"
                  href={KORAIL_PAYMENT_URL}
                  target="_blank"
                  rel="noreferrer"
                >
                  결제하기
                </a>
              )}
              <button className="kb-btn kb-btn--danger" onClick={() => onCancel(r)}>
                {r.waiting ? '예약대기 취소' : '예약 취소'}
              </button>
            </div>
          </Ticket>
        ))}
      </div>
      <p className="kb-hint kb-tickets-note">
        결제 대기: 아직 결제하지 않은 예약입니다. 기한이 지나면 코레일이 자동으로 취소합니다.
      </p>
    </>
  )
}

function IssuedList({ account }: { account: KorailAccount }) {
  if (account.tickets.length === 0) {
    return (
      <div className="kb-empty">
        <p>발권한 승차권이 없습니다.</p>
      </div>
    )
  }
  return (
    <>
      <div className="kb-list">
        {account.tickets.map((t) => (
          <Ticket
            key={`${t.rsv_id}-${t.train_no}-${t.car_no}-${t.seats}`}
            tone="success"
            stub={
              <>
                <span>
                  <b className="kb-num">
                    {t.car_no}호차 {t.seats}
                  </b>
                </span>
                <span>
                  예약번호 <span className="kb-num">{t.rsv_id}</span>
                </span>
              </>
            }
          >
            <div className="kb-ticket__head">
              <span>
                <TrainName item={t} />
                <Source item={t} />
              </span>
              <span className="kb-badge kb-badge--success">발권 완료</span>
            </div>
            <RouteLine
              size="lg"
              from={t.dep_station}
              to={t.arr_station}
              fromTime={formatTime(t.dep_time)}
              toTime={formatTime(t.arr_time)}
              status="success"
            />
            <div className="kb-ticket__window">
              {formatDate(t.dep_date)} · <span className="kb-num">{formatWon(t.price)}</span> ·{' '}
              {t.seat_count}석
            </div>
          </Ticket>
        ))}
      </div>
      <p className="kb-hint kb-tickets-note">
        발권한 승차권의 환불·변경은 코레일톡 앱이나 홈페이지에서 해주세요.
      </p>
    </>
  )
}

function TrainName({ item }: { item: KorailTrain }) {
  return (
    <span className={`kb-tag${item.train_type.startsWith('KTX') ? ' kb-tag--brand' : ''}`}>
      {item.train_type} {trainNumber(item.train_no)}
    </span>
  )
}

/** 코레일봇이 잡은 예약이면 그 예약 기록으로 가는 링크 */
function Source({ item }: { item: KorailTrain }) {
  return item.reservation_id ? (
    <Link className="kb-src kb-src--bot" to={`/r/${item.reservation_id}`}>
      코레일봇 예약
    </Link>
  ) : (
    <span className="kb-src">직접 예약</span>
  )
}

function LoadingTicket() {
  return (
    <>
      <div className="kb-list">
        <Ticket stub={<span>&nbsp;</span>}>
          <div className="kb-tickets-loading">
            <span className="kb-spinner" />
            <span className="kb-caption">코레일에서 불러오는 중…</span>
          </div>
        </Ticket>
      </div>
      <p className="kb-hint kb-tickets-note">
        예약이 많으면 몇 초 걸릴 수 있습니다 (예약마다 코레일에 한 번씩 더 조회).
      </p>
    </>
  )
}

/** 남은 시간 표시를 갱신하기 위한 현재 시각 */
function useNow(interval: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), interval)
    return () => window.clearInterval(timer)
  }, [interval])
  return now
}
