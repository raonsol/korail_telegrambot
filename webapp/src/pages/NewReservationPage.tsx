import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Api, type Reservation, type ReservationInput, type SeatType, type TrainType } from '../api'
import { ErrorBox } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { Segmented } from '../components/Segmented'
import { StationInput } from '../components/StationInput'
import { compactToIso, formatDate, kstNow, roundUpTime } from '../format'
import { recentStations, rememberStations } from '../storage'

const MAX_DAYS_AHEAD = 90

const TRAIN_OPTIONS: { value: TrainType; label: string }[] = [
  { value: 'KTX', label: 'KTX' },
  { value: 'ALL', label: '모든 열차' },
]

const SEAT_OPTIONS: { value: SeatType; label: string; hint: string }[] = [
  { value: 'general', label: '일반실 우선', hint: '없으면 특실' },
  { value: 'general_only', label: '일반실만', hint: '특실 제외' },
  { value: 'special', label: '특실 우선', hint: '없으면 일반실' },
  { value: 'special_only', label: '특실만', hint: '일반실 제외' },
]

/** 예약대기를 신청할 수 있는 좌석 옵션 (코레일의 예약대기 가능 여부는 일반실 기준) */
const WAITLIST_SEAT_TYPES: SeatType[] = ['general', 'general_only', 'special']

function hhmm(time: string) {
  return time.replace(':', '')
}

function toTimeInput(value: string) {
  return `${value.slice(0, 2)}:${value.slice(2, 4)}`
}

export function NewReservationPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const prefill = (location.state as { prefill?: Reservation } | null)?.prefill

  const now = kstNow()
  const today = now.date
  const maxDate = kstNow(MAX_DAYS_AHEAD).date
  const initialDate =
    prefill && compactToIso(prefill.dep_date) >= today ? compactToIso(prefill.dep_date) : today
  const defaultDepTime = initialDate === today ? roundUpTime(now.time) : '06:00'

  const [date, setDate] = useState(initialDate)
  const [src, setSrc] = useState(prefill?.src_station ?? '')
  const [dst, setDst] = useState(prefill?.dst_station ?? '')
  const [depTime, setDepTime] = useState(
    prefill && initialDate !== today ? toTimeInput(prefill.dep_time) : defaultDepTime,
  )
  const [maxTime, setMaxTime] = useState(prefill ? toTimeInput(prefill.max_dep_time) : '23:59')
  const [trainType, setTrainType] = useState<TrainType>(prefill?.train_type ?? 'KTX')
  const [seatType, setSeatType] = useState<SeatType>(prefill?.seat_type ?? 'general')
  const [waitlist, setWaitlist] = useState(prefill?.allow_waitlist ?? false)
  const waitlistAvailable = WAITLIST_SEAT_TYPES.includes(seatType)
  const [confirming, setConfirming] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const recent = useMemo(() => recentStations(), [])

  const create = useMutation({
    mutationFn: (input: ReservationInput) => Api.createReservation(input),
    onSuccess: (reservation) => {
      rememberStations(reservation.src_station, reservation.dst_station)
      queryClient.setQueryData(['reservation', reservation.id], reservation)
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
      navigate(`/r/${reservation.id}`, { replace: true, state: { created: true } })
    },
  })

  const validate = (): string | null => {
    if (!src) return '출발역을 검색해서 선택해주세요.'
    if (!dst) return '도착역을 검색해서 선택해주세요.'
    if (src === dst) return '출발역과 도착역이 같습니다.'
    if (date < today) return '출발일이 오늘보다 이전입니다.'
    if (date === today && depTime < kstNow().time) return '출발 시각이 현재 시각보다 이전입니다.'
    if (maxTime < depTime) return '최대 출발 시각이 출발 시각보다 이전입니다.'
    return null
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const message = validate()
    setFormError(message)
    if (!message) setConfirming(true)
  }

  const swap = () => {
    setSrc(dst)
    setDst(src)
  }

  const input: ReservationInput = {
    dep_date: date,
    src_station: src,
    dst_station: dst,
    dep_time: hhmm(depTime),
    max_dep_time: hhmm(maxTime),
    train_type: trainType,
    seat_type: seatType,
    allow_waitlist: waitlistAvailable && waitlist,
  }

  return (
    <Layout title="새 예약" back>
      <form className="form" onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="date">출발일</label>
          <input
            id="date"
            type="date"
            min={today}
            max={maxDate}
            value={date}
            onChange={(e) => setDate(e.target.value)}
            required
          />
        </div>

        <div className="stations">
          <div className="stations-inputs">
            <StationInput label="출발역" value={src} onChange={setSrc} recent={recent} exclude={dst} />
            <StationInput label="도착역" value={dst} onChange={setDst} recent={recent} exclude={src} />
          </div>
          <button type="button" className="swap-button" onClick={swap} aria-label="출발역과 도착역 바꾸기">
            <Icon name="swap" size={18} />
          </button>
        </div>

        <fieldset className="field">
          <legend>출발 시각 범위</legend>
          <div className="time-range">
            <input
              type="time"
              aria-label="이 시각 이후 출발"
              min={date === today ? now.time : undefined}
              value={depTime}
              onChange={(e) => setDepTime(e.target.value)}
              required
            />
            <span>~</span>
            <input
              type="time"
              aria-label="이 시각 이전 출발"
              value={maxTime}
              onChange={(e) => setMaxTime(e.target.value)}
              required
            />
          </div>
          <small className="muted">이 시간대에 출발하는 열차 중 빈 좌석을 찾아 예약합니다.</small>
        </fieldset>

        <div className="field">
          <span className="label">열차 종류</span>
          <Segmented name="열차 종류" value={trainType} options={TRAIN_OPTIONS} onChange={setTrainType} />
        </div>

        <div className="field">
          <span className="label">좌석</span>
          <Segmented
            name="좌석"
            value={seatType}
            options={SEAT_OPTIONS}
            onChange={setSeatType}
            columns={2}
          />
        </div>

        <div className="field waitlist-field">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={waitlistAvailable && waitlist}
              disabled={!waitlistAvailable}
              onChange={(e) => setWaitlist(e.target.checked)}
            />
            모두 매진이면 예약대기 신청
          </label>
          <small className="muted">
            {waitlistAvailable
              ? '빈 좌석이 없고 예약대기가 열려 있으면 일반실 예약대기를 걸고 마칩니다.'
              : '특실만 예약에는 예약대기를 쓸 수 없습니다 (코레일 예약대기는 일반실 기준).'}
          </small>
          <details className="help">
            <summary>예약대기란?</summary>
            <p>
              매진된 열차에 대기를 걸어 두면, 취소표가 생겼을 때 코레일이 신청 순서대로 좌석을
              배정해 주는 코레일의 기능입니다.
            </p>
            <ol>
              <li>지금처럼 빈 좌석을 찾다가, 좌석이 있는 열차가 있으면 좌석을 먼저 예약합니다.</li>
              <li>
                시간 범위의 열차가 모두 매진이고 예약대기가 열려 있으면, 가장 이른 열차에 일반실
                예약대기를 신청하고 예약 작업을 끝냅니다. 대기를 신청한 뒤에는 취소표를 더 찾지
                않습니다.
              </li>
              <li>
                좌석이 배정되면 코레일이 문자·앱으로 알려주며, 안내받은 기한 안에 직접 결제해야
                합니다.
              </li>
            </ol>
            <ul>
              <li>예약대기는 일반실만 신청합니다. 특실 우선 예약이어도 일반실로 신청합니다.</li>
              <li>신청했다고 좌석이 보장되지는 않습니다.</li>
              <li>
                신청 내역은 코레일톡 앱 → 승차권 예매 → 예약 승차권 조회/취소에서 확인·취소할 수
                있습니다.
              </li>
              <li>끄면 최대 실행 시간 동안 취소표(빈 좌석)만 계속 찾습니다.</li>
            </ul>
          </details>
        </div>

        {formError && <div className="error-box" role="alert">{formError}</div>}

        <button className="button primary block large">예약 조건 확인</button>
      </form>

      {confirming && (
        <div className="sheet-backdrop" onClick={() => !create.isPending && setConfirming(false)}>
          <div
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="confirm-title">이 조건으로 예약을 시작할까요?</h2>
            <dl className="summary">
              <dt>출발일</dt>
              <dd>{formatDate(date.replaceAll('-', ''))}</dd>
              <dt>구간</dt>
              <dd>
                {src} → {dst}
              </dd>
              <dt>출발 시각</dt>
              <dd>
                {depTime} ~ {maxTime}
              </dd>
              <dt>열차</dt>
              <dd>{TRAIN_OPTIONS.find((o) => o.value === trainType)?.label}</dd>
              <dt>좌석</dt>
              <dd>{SEAT_OPTIONS.find((o) => o.value === seatType)?.label}</dd>
              <dt>예약대기</dt>
              <dd>{input.allow_waitlist ? '사용 (모두 매진이면 일반실 대기)' : '사용 안 함'}</dd>
            </dl>
            <p className="muted small">
              예약에 성공하면 알림을 보내드립니다. 결제는 예약 후 20분 안에 코레일에서 직접 해야
              합니다.
              {input.allow_waitlist &&
                ' 예약대기를 신청하면 좌석이 배정된 뒤 코레일이 알려주며, 그때 결제합니다.'}
            </p>
            <ErrorBox error={create.error} />
            <div className="sheet-actions">
              <button
                type="button"
                className="button ghost"
                onClick={() => setConfirming(false)}
                disabled={create.isPending}
              >
                수정
              </button>
              <button
                type="button"
                className="button primary"
                onClick={() => create.mutate(input)}
                disabled={create.isPending}
              >
                {create.isPending ? '시작하는 중…' : '예약 시작'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Layout>
  )
}
