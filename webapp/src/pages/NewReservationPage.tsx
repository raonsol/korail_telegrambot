import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Api, type Reservation, type ReservationInput, type Station, type TrainType } from '../api'
import { ErrorBox } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { SeatPicker } from '../components/SeatPicker'
import { TrainTag } from '../components/StatusBadge'
import { Segmented } from '../components/Segmented'
import { Dialog, Sheet } from '../components/Sheet'
import { Switch } from '../components/Switch'
import {
  SEAT_SUMMARY,
  compactToIso,
  formatDate,
  hourLabel,
  kstNow,
  seatSelectionFrom,
  toSeatType,
  type SeatSelection,
} from '../format'
import { recentStations, rememberStations } from '../storage'

/** 서버 검증(core/schemas.py MAX_DAYS_AHEAD)과 같은 범위 */
const MAX_DAYS_AHEAD = 90

/** 검색 전에 보여 줄 주요 역 (최근 이용 역 다음에) */
const MAJOR_STATIONS = ['서울', '용산', '광명', '오송', '대전', '동대구', '부산', '광주송정', '강릉']

const TRAIN_OPTIONS: { value: TrainType; label: string; hint: string }[] = [
  { value: 'KTX', label: 'KTX만', hint: 'KTX·KTX-산천' },
  { value: 'ALL', label: '모든 열차', hint: 'ITX·무궁화 포함' },
]

type Field = 'src' | 'dst' | 'date' | 'time' | 'seat'
type SheetKey = Field | 'train'

const FIELD_ORDER: Field[] = ['src', 'dst', 'date', 'time', 'seat']

const SHEET_TITLES: Record<SheetKey, string> = {
  src: '출발역',
  dst: '도착역',
  date: '출발일',
  time: '출발 시각 범위',
  train: '열차 종류',
  seat: '좌석',
}

const NEXT_LABELS: Record<Field, string> = {
  src: '출발역 선택',
  dst: '도착역 선택',
  date: '출발일 선택',
  time: '출발 시각 선택',
  seat: '좌석 선택',
}

interface Preset {
  key: string
  label: string
  start: number
  end: number
  disabled?: boolean
}

interface Draft {
  src: string
  dst: string
  date: string | null // YYYY-MM-DD (KST)
  start: number | null // 시 (0~23)
  end: number | null // 시 (1~24, 24 = 23:59)
  preset: string
  train: TrainType
  seat: SeatSelection
  seatDone: boolean
  waitlist: boolean
}

const EMPTY_SEAT: SeatSelection = { general: true, special: false, priority: 'general' }

function initialDraft(prefill: Reservation | undefined, today: string): Draft {
  if (!prefill) {
    return {
      src: '서울',
      dst: '',
      date: null,
      start: null,
      end: null,
      preset: '',
      train: 'KTX',
      seat: EMPTY_SEAT,
      seatDone: false,
      waitlist: false,
    }
  }
  // "같은 조건으로 다시 예약" — 시각은 정각 단위로 맞춤
  const date = compactToIso(prefill.dep_date)
  const start = Number(prefill.dep_time.slice(0, 2))
  const maxH = Number(prefill.max_dep_time.slice(0, 2))
  const maxM = Number(prefill.max_dep_time.slice(2, 4))
  const end = prefill.max_dep_time >= '2359' ? 24 : Math.min(24, maxM > 0 ? maxH + 1 : maxH)
  return {
    src: prefill.src_station,
    dst: prefill.dst_station,
    date: date >= today ? date : null,
    start,
    end: Math.max(end, start + 1),
    preset: '',
    train: prefill.train_type,
    seat: seatSelectionFrom(prefill.seat_type),
    seatDone: true,
    waitlist: prefill.allow_waitlist,
  }
}

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

/** HH:MM → 1분 뒤 HHMM (오늘 출발이면 지난 시각 대신 지금부터 찾음) */
function nextMinute(time: string): string {
  const [h, m] = time.split(':').map(Number)
  const total = Math.min(h * 60 + m + 1, 23 * 60 + 59)
  return `${pad2(Math.floor(total / 60))}${pad2(total % 60)}`
}

export function NewReservationPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const prefill = (location.state as { prefill?: Reservation } | null)?.prefill

  const now = kstNow()
  const today = now.date
  const maxDate = kstNow(MAX_DAYS_AHEAD).date
  const nowHour = Number(now.time.slice(0, 2))

  const [draft, setDraft] = useState<Draft>(() => initialDraft(prefill, today))
  const [sheet, setSheet] = useState<SheetKey | null>(null)
  const [waitInfoOpen, setWaitInfoOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const update = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }))
    setFormError(null)
  }

  const seatType = toSeatType(draft.seat)
  const seatFilled = draft.seatDone && seatType !== null
  const hasTime = draft.start !== null && draft.end !== null
  const filled: Record<Field, boolean> = {
    src: draft.src !== '',
    dst: draft.dst !== '',
    date: draft.date !== null,
    time: hasTime,
    seat: seatFilled,
  }
  const filledCount = FIELD_ORDER.filter((f) => filled[f]).length
  const ready = filledCount === FIELD_ORDER.length
  const nextField = FIELD_ORDER.find((f) => !filled[f])
  // 코레일 예약대기는 일반실 기준이라 특실만 예약에는 쓸 수 없음
  const waitlistAvailable = seatType !== 'special_only'

  // '지금부터'는 오늘 출발일 때만, 오늘 이미 지난 시간대는 고를 수 없음
  const isToday = draft.date === today
  const nowPreset: Preset = { key: 'now', label: '지금부터', start: nowHour, end: 24 }
  const presets: Preset[] = [
    ...(isToday ? [nowPreset] : []),
    { key: 'am', label: '오전', start: 6, end: 12 },
    { key: 'pm', label: '오후', start: 12, end: 18 },
    { key: 'eve', label: '저녁', start: 18, end: 24 },
  ].map((p) => ({ ...p, disabled: isToday && p.end <= nowHour }))

  const openSheet = (key: SheetKey) => {
    // 시각 시트를 처음 열면 오늘은 지금부터, 다른 날은 오전(06~12)을 기본으로
    if (key === 'time' && !hasTime) {
      const p = isToday ? nowPreset : presets.find((x) => x.key === 'am')!
      update({ start: p.start, end: p.end, preset: p.key })
    }
    setSheet(key)
  }
  const closeSheet = () => setSheet(null)

  const create = useMutation({
    mutationFn: (input: ReservationInput) => Api.createReservation(input),
    onSuccess: (reservation) => {
      rememberStations(reservation.src_station, reservation.dst_station)
      queryClient.setQueryData(['reservation', reservation.id], reservation)
      queryClient.invalidateQueries({ queryKey: ['reservations'] })
      navigate(`/r/${reservation.id}`, { replace: true, state: { created: true } })
    },
  })

  const submit = () => {
    if (!ready || draft.date === null || draft.start === null || draft.end === null) return
    const current = kstNow()
    if (draft.date < current.date) {
      setFormError('출발일이 지났습니다. 출발일을 다시 선택해주세요.')
      return
    }
    let depTime = `${pad2(draft.start)}00`
    const maxDepTime = draft.end >= 24 ? '2359' : `${pad2(draft.end)}00`
    if (draft.date === current.date && depTime <= current.time.replace(':', '')) {
      depTime = nextMinute(current.time)
    }
    if (maxDepTime < depTime) {
      setFormError('선택한 시간대의 열차는 이미 모두 출발했습니다. 출발 시각을 다시 선택해주세요.')
      return
    }
    create.mutate({
      dep_date: draft.date,
      src_station: draft.src,
      dst_station: draft.dst,
      dep_time: depTime,
      max_dep_time: maxDepTime,
      train_type: draft.train,
      seat_type: seatType ?? 'general',
      allow_waitlist: waitlistAvailable && draft.waitlist,
    })
  }

  const reset = () => {
    // 열차 종류는 유지
    update({
      src: '',
      dst: '',
      date: null,
      start: null,
      end: null,
      preset: '',
      seat: EMPTY_SEAT,
      seatDone: false,
      waitlist: false,
    })
  }

  const rows: { field: Field; label: string; value: string | null; mono?: boolean }[] = [
    {
      field: 'date',
      label: '출발일',
      value: draft.date ? formatDate(draft.date.replaceAll('-', '')) : null,
    },
    {
      field: 'time',
      label: '시각',
      value: hasTime ? `${hourLabel(draft.start!)} ~ ${hourLabel(draft.end!)}` : null,
      mono: true,
    },
    { field: 'seat', label: '좌석', value: seatFilled ? SEAT_SUMMARY[seatType!] : null },
  ]

  return (
    <Layout
      title="새 예약"
      back
      actions={
        <button className="kb-btn kb-btn--ghost kb-btn--sm" onClick={reset}>
          초기화
        </button>
      }
      footer={
        <>
          {(formError || create.error) && <ErrorBox error={formError ?? create.error} />}
          {ready ? (
            <button
              className="kb-btn kb-btn--primary kb-btn--lg kb-btn--block"
              disabled={create.isPending}
              onClick={submit}
            >
              {create.isPending ? '시작하는 중…' : '이 승차권으로 예약 시작'}
            </button>
          ) : (
            <button
              className="kb-btn kb-btn--primary kb-btn--lg kb-btn--block"
              onClick={() => nextField && openSheet(nextField)}
            >
              {nextField && NEXT_LABELS[nextField]}
            </button>
          )}
          <span className="kb-fineprint">결제는 예약 후 20분 안에 코레일에서 직접 해야 합니다.</span>
        </>
      }
    >
      <div className="kb-stack kb-stack--loose">
        <p className="kb-guide">
          {ready
            ? '조건이 모두 채워졌습니다. 승차권을 확인하고 예약을 시작하세요.'
            : '승차권의 빈 칸을 눌러 채워주세요.'}
        </p>

        <div className={`kb-ticket kb-ticket-form${ready ? ' kb-ticket--brand' : ''}`}>
          <div className="kb-ticket__paper">
            <div className="kb-ticket__main">
              <div className="kb-ticket__head">
                <button
                  type="button"
                  className="kb-tag-btn"
                  aria-label={`열차 종류: ${draft.train === 'KTX' ? 'KTX' : '모든 열차'} (바꾸기)`}
                  onClick={() => openSheet('train')}
                >
                  <TrainTag
                    reservation={{
                      train_type: draft.train,
                      train_type_label: draft.train === 'KTX' ? 'KTX' : '모든 열차',
                    }}
                  />
                </button>
                <span className="kb-caption">코레일봇 예약 조건</span>
              </div>

              <div className="kb-ticket-form__route">
                <StopButton
                  label="출발"
                  value={draft.src}
                  open={sheet === 'src'}
                  onClick={() => openSheet('src')}
                />
                <div
                  className="kb-route kb-route--mini"
                  data-status={draft.src && draft.dst ? (ready ? 'running' : 'success') : 'queued'}
                  aria-hidden="true"
                >
                  <div className="kb-route__track" />
                </div>
                <StopButton
                  label="도착"
                  end
                  value={draft.dst}
                  open={sheet === 'dst'}
                  onClick={() => openSheet('dst')}
                />
              </div>

              <div className="kb-rows">
                {rows.map((row) => (
                  <button
                    key={row.field}
                    type="button"
                    className={`kb-rowbtn${row.value ? '' : ' is-empty'}`}
                    onClick={() => openSheet(row.field)}
                  >
                    <span>{row.label}</span>
                    <b className={row.value && row.mono ? 'kb-num' : undefined}>{row.value ?? '선택'}</b>
                    <Icon name="chevron" size={16} />
                  </button>
                ))}
              </div>
            </div>

            <div className="kb-ticket__stub">
              <span className="kb-waitlist">
                <small>{waitlistAvailable ? '모두 매진이면' : '특실만 예약에는 쓸 수 없음'}</small>
                <span>
                  <b>예약대기 신청</b>
                  <button
                    type="button"
                    className="kb-info-btn"
                    aria-label="예약대기 설명"
                    onClick={() => setWaitInfoOpen(true)}
                  >
                    <Icon name="info" size={16} />
                  </button>
                </span>
              </span>
              <Switch
                checked={waitlistAvailable && draft.waitlist}
                disabled={!waitlistAvailable}
                onChange={() => update({ waitlist: !draft.waitlist })}
                label="모두 매진이면 예약대기 신청"
              />
            </div>
          </div>
        </div>

        <div className="kb-progress" aria-label={`${filledCount}/5 입력됨`}>
          {FIELD_ORDER.map((f) => (
            <i key={f} className={filled[f] ? 'is-done' : undefined} />
          ))}
          <span className="kb-caption kb-num">
            {filledCount}/{FIELD_ORDER.length}
          </span>
        </div>
      </div>

      {sheet && (
        <Sheet title={SHEET_TITLES[sheet]} onClose={closeSheet}>
          {(sheet === 'src' || sheet === 'dst') && (
            <StationPicker
              value={sheet === 'src' ? draft.src : draft.dst}
              exclude={sheet === 'src' ? draft.dst : draft.src}
              onSelect={(name) => {
                update(sheet === 'src' ? { src: name } : { dst: name })
                closeSheet()
              }}
            />
          )}

          {sheet === 'date' && (
            <CalendarPicker
              value={draft.date}
              min={today}
              max={maxDate}
              onSelect={(date) => {
                // 오늘로 바꿨는데 고른 시간대가 이미 지났으면 지금부터로
                const expired = date === today && draft.end !== null && draft.end <= nowHour
                update(expired ? { date, start: nowHour, end: 24, preset: 'now' } : { date })
                closeSheet()
              }}
            />
          )}

          {sheet === 'time' && draft.start !== null && draft.end !== null && (
            <TimePicker
              start={draft.start}
              end={draft.end}
              preset={draft.preset}
              presets={presets}
              onChange={(start, end, preset = '') => update({ start, end, preset })}
              onDone={closeSheet}
            />
          )}

          {sheet === 'train' && (
            <Segmented
              name="열차 종류"
              value={draft.train}
              options={TRAIN_OPTIONS}
              onChange={(train) => {
                update({ train })
                closeSheet()
              }}
            />
          )}

          {sheet === 'seat' && (
            <div className="kb-sheet-body">
              <SeatPicker value={draft.seat} onChange={(seat) => update({ seat })} />
              <button
                type="button"
                className="kb-btn kb-btn--primary kb-btn--block"
                disabled={seatType === null}
                onClick={() => {
                  update({ seatDone: true })
                  closeSheet()
                }}
              >
                완료
              </button>
            </div>
          )}
        </Sheet>
      )}

      {waitInfoOpen && (
        <Dialog title="예약대기란?" icon={<Icon name="info" size={18} />} onClose={() => setWaitInfoOpen(false)}>
          <p>
            매진된 열차에 대기를 걸어 두면, 취소표가 생겼을 때 코레일이 신청 순서대로 좌석을 배정해
            주는 코레일의 기능입니다.
          </p>
          <ul>
            <li>빈 좌석이 없고 예약대기가 열려 있으면 일반실 예약대기를 걸고 마칩니다.</li>
            <li>좌석이 배정되면 코레일에서 안내 문자가 오며, 정해진 시간 안에 결제해야 합니다.</li>
            <li>
              신청 내역은 코레일톡 앱 → 승차권 예매 → 예약 승차권 조회/취소에서 확인·취소할 수
              있습니다.
            </li>
          </ul>
          <button className="kb-btn kb-btn--primary kb-btn--block" onClick={() => setWaitInfoOpen(false)}>
            확인
          </button>
        </Dialog>
      )}
    </Layout>
  )
}

function StopButton({
  label,
  value,
  open,
  end,
  onClick,
}: {
  label: string
  value: string
  open: boolean
  end?: boolean
  onClick: () => void
}) {
  const cls = ['kb-stop-btn', end && 'kb-stop-btn--end', !value && 'is-empty', open && 'is-open']
    .filter(Boolean)
    .join(' ')
  return (
    <button type="button" className={cls} onClick={onClick}>
      <small>{label}</small>
      <b>{value || '선택'}</b>
    </button>
  )
}

/**
 * 역 검색 + 선택 (봇과 같이 검색 결과에서 골라야 확정 → 오타 방지).
 * 검색 전에는 최근 이용 역과 주요 역을 보여 줌
 */
function StationPicker({
  value,
  exclude,
  onSelect,
}: {
  value: string
  exclude: string
  onSelect: (station: string) => void
}) {
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [results, setResults] = useState<Station[]>([])
  const [suggestions] = useState(() => [...new Set([...recentStations(), ...MAJOR_STATIONS])].slice(0, 12))

  // 입력 디바운스
  useEffect(() => {
    const trimmed = text.trim()
    const timer = setTimeout(
      () => {
        setQuery(trimmed)
        setPage(1)
      },
      trimmed ? 300 : 0,
    )
    return () => clearTimeout(timer)
  }, [text])

  const search = useQuery({
    queryKey: ['stations', query, page],
    queryFn: () => Api.stations(query, page),
    enabled: query.length > 0,
    staleTime: 60 * 60 * 1000,
  })

  useEffect(() => {
    if (!search.data) return
    setResults((prev) => (page === 1 ? search.data.stations : [...prev, ...search.data.stations]))
  }, [search.data, page])

  const names = query ? results.map((s) => s.name) : suggestions
  const hasMore = query && search.data ? page * search.data.page_size < search.data.total : false

  return (
    <div className="kb-sheet-body">
      <div className="kb-input-wrap">
        <Icon name="search" size={18} />
        <input
          className="kb-input"
          value={text}
          autoComplete="off"
          enterKeyHint="search"
          placeholder="역 이름 검색"
          aria-label="역 이름 검색"
          onChange={(e) => setText(e.target.value)}
        />
      </div>

      {query && search.isError ? (
        <div className="kb-station-empty">{(search.error as Error).message}</div>
      ) : query && search.isFetching && page === 1 ? (
        <div className="kb-station-empty">검색 중…</div>
      ) : query && search.data && results.length === 0 ? (
        <div className="kb-station-empty">'{query}' 검색 결과가 없습니다</div>
      ) : (
        <div className="kb-station-grid">
          {[...new Set(names)].map((name) => (
            <button
              key={name}
              type="button"
              className={`kb-station-btn${name === value ? ' is-selected' : ''}`}
              disabled={name === exclude}
              onClick={() => onSelect(name)}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {hasMore && (
        <button
          type="button"
          className="kb-btn kb-btn--ghost kb-btn--sm kb-station-more"
          disabled={search.isFetching}
          onClick={() => setPage((p) => p + 1)}
        >
          {search.isFetching ? '불러오는 중…' : '더 보기'}
        </button>
      )}
    </div>
  )
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number)
  const index = y * 12 + (m - 1) + delta
  return `${Math.floor(index / 12)}-${pad2((index % 12) + 1)}`
}

/** 달력 (날짜는 모두 KST 기준 YYYY-MM-DD 문자열로 비교) */
function CalendarPicker({
  value,
  min,
  max,
  onSelect,
}: {
  value: string | null
  min: string
  max: string
  onSelect: (date: string) => void
}) {
  const [month, setMonth] = useState((value ?? min).slice(0, 7))
  const [y, m] = month.split('-').map(Number)
  const firstDow = new Date(Date.UTC(y, m - 1, 1)).getUTCDay()
  const dayCount = new Date(Date.UTC(y, m, 0)).getUTCDate()
  const canPrev = month > min.slice(0, 7)
  const canNext = month < max.slice(0, 7)

  return (
    <div className="kb-sheet-body kb-sheet-body--calendar">
      <div className="kb-calendar__head">
        <div className="kb-calendar__nav">
          <button
            type="button"
            className="kb-icon-btn"
            aria-label="이전 달"
            disabled={!canPrev}
            onClick={() => setMonth(shiftMonth(month, -1))}
          >
            <Icon name="back" size={18} />
          </button>
          <strong aria-live="polite">
            {y}년 {m}월
          </strong>
          <button
            type="button"
            className="kb-icon-btn"
            aria-label="다음 달"
            disabled={!canNext}
            onClick={() => setMonth(shiftMonth(month, 1))}
          >
            <Icon name="chevron" size={18} />
          </button>
        </div>
        <span className="kb-caption">오늘부터 예매 가능한 날짜</span>
      </div>
      <div className="kb-calendar">
        {WEEKDAYS.map((name, i) => (
          <span
            key={name}
            className={`kb-calendar__dow${i === 0 ? ' is-sun' : i === 6 ? ' is-sat' : ''}`}
          >
            {name}
          </span>
        ))}
        {Array.from({ length: firstDow }, (_, i) => (
          <span key={`blank-${i}`} />
        ))}
        {Array.from({ length: dayCount }, (_, i) => {
          const day = i + 1
          const iso = `${month}-${pad2(day)}`
          const dow = (firstDow + i) % 7
          const cls = [
            'kb-calendar__day',
            dow === 0 && 'is-sun',
            dow === 6 && 'is-sat',
            iso === min && 'is-today',
            iso === value && 'is-selected',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <button
              key={iso}
              type="button"
              className={cls}
              disabled={iso < min || iso > max}
              aria-label={formatDate(iso.replaceAll('-', ''))}
              aria-pressed={iso === value}
              onClick={() => onSelect(iso)}
            >
              {day}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** 출발 시각 범위 (1시간 단위, 시작 < 끝, 24 = 23:59) */
function TimePicker({
  start,
  end,
  preset,
  presets,
  onChange,
  onDone,
}: {
  start: number
  end: number
  preset: string
  presets: Preset[]
  onChange: (start: number, end: number, preset?: string) => void
  onDone: () => void
}) {
  return (
    <div className="kb-sheet-body kb-sheet-body--time">
      <div className="kb-filter" role="radiogroup" aria-label="시간대">
        {presets.map((p) => (
          <button
            key={p.key}
            type="button"
            role="radio"
            aria-checked={preset === p.key}
            disabled={p.disabled}
            className={preset === p.key ? 'is-selected' : ''}
            onClick={() => onChange(p.start, p.end, p.key)}
          >
            {p.label}
            <small>{p.key === 'now' ? `${pad2(p.start)}–` : `${pad2(p.start)}–${pad2(p.end)}`}</small>
          </button>
        ))}
      </div>

      <div aria-hidden="true">
        <div className="kb-timebar">
          <div className="kb-timebar__track" />
          <div
            className="kb-timebar__range"
            style={{ left: `${(start / 24) * 100}%`, width: `${((end - start) / 24) * 100}%` }}
          />
        </div>
        <div className="kb-timebar__ticks">
          <span>0</span>
          <span>6</span>
          <span>12</span>
          <span>18</span>
          <span>24</span>
        </div>
      </div>

      <div className="kb-row">
        <Stepper
          label="이후 출발"
          value={hourLabel(start)}
          canDec={start > 0}
          canInc={start < end - 1}
          onDec={() => onChange(start - 1, end)}
          onInc={() => onChange(start + 1, end)}
        />
        <Stepper
          label="이전 출발"
          value={hourLabel(end)}
          canDec={end > start + 1}
          canInc={end < 24}
          onDec={() => onChange(start, end - 1)}
          onInc={() => onChange(start, end + 1)}
        />
      </div>

      <button type="button" className="kb-btn kb-btn--primary kb-btn--block" onClick={onDone}>
        완료
      </button>
    </div>
  )
}

function Stepper({
  label,
  value,
  canDec,
  canInc,
  onDec,
  onInc,
}: {
  label: string
  value: string
  canDec: boolean
  canInc: boolean
  onDec: () => void
  onInc: () => void
}) {
  return (
    <div className="kb-stepper">
      <span className="kb-hint">{label}</span>
      <div className="kb-stepper__box">
        <button type="button" aria-label={`${label} 1시간 앞으로`} disabled={!canDec} onClick={onDec}>
          −
        </button>
        <span aria-live="polite">{value}</span>
        <button type="button" aria-label={`${label} 1시간 뒤로`} disabled={!canInc} onClick={onInc}>
          +
        </button>
      </div>
    </div>
  )
}
