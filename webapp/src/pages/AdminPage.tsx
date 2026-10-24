import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type CSSProperties, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Api, type AdminUser, type Reservation } from '../api'
import { ErrorBox, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Logo } from '../components/Layout'
import { StatusBadge, TrainTag } from '../components/StatusBadge'
import { RouteLine } from '../components/Ticket'
import { useOnline } from '../hooks'
import {
  SEAT_SHORT,
  STATUS_LABELS,
  formatDate,
  formatDateTime,
  formatPhoneInput,
  parseResultText,
  timeRange,
} from '../format'

type Tab = 'reservations' | 'users'
type StatusFilter = 'all' | 'active' | 'success' | 'failed' | 'cancelled'
type Sort = 'started' | 'date' | 'attempts'

const RESERVATIONS_KEY = ['reservations', 'all', 'all']
const USERS_KEY = ['admin', 'users']

const FILTERS: { value: StatusFilter; label: string; match: (r: Reservation) => boolean }[] = [
  { value: 'all', label: '전체', match: () => true },
  { value: 'active', label: '진행 중', match: (r) => r.is_active },
  { value: 'success', label: '성공', match: (r) => r.status === 'success' },
  { value: 'failed', label: '실패·오류', match: (r) => r.status === 'failed' || r.status === 'error' },
  { value: 'cancelled', label: '취소됨', match: (r) => r.status === 'cancelled' },
]

const SORTS: Record<Sort, (a: Reservation, b: Reservation) => number> = {
  started: (a, b) => b.created_at.localeCompare(a.created_at),
  date: (a, b) => (a.dep_date + a.dep_time).localeCompare(b.dep_date + b.dep_time),
  attempts: (a, b) => b.attempts - a.attempts,
}

const RESERVATION_COLS =
  '36px 124px minmax(180px,1.4fr) 116px 128px minmax(150px,1fr) minmax(190px,1.1fr) 84px 148px 76px'
const USER_COLS = '150px minmax(90px,1fr) 90px 100px 80px 90px 150px 150px'

function tableStyle(cols: string, minWidth: number) {
  return { '--cols': cols, '--table-min': `${minWidth}px` } as CSSProperties
}

/** 01012345678 → 010-1234-5678 (전화번호가 아닌 계정은 그대로) */
function displayPhone(id: string): string {
  if (/^\d{11}$/.test(id)) return formatPhoneInput(id)
  if (/^\d{10}$/.test(id)) return `${id.slice(0, 3)}-${id.slice(3, 6)}-${id.slice(6)}`
  return id
}

/** 검색어와 일치하는 부분 강조 */
function highlight(text: string, q: string): ReactNode {
  if (!q) return text
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return text
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  )
}

const kstClock = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  month: 'long',
  day: 'numeric',
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
})

export function AdminPage() {
  const [tab, setTab] = useState<Tab>('reservations')
  const online = useOnline()
  const reservations = useQuery({
    queryKey: RESERVATIONS_KEY,
    // 진행 중은 따로 받아 이력이 많아도 빠지지 않게 함 (서버 limit 최대 500)
    queryFn: async () => {
      const [active, history] = await Promise.all([
        Api.reservations('active', 'all', 500),
        Api.reservations('history', 'all', 500),
      ])
      return [...active, ...history]
    },
    // 시도 횟수 갱신 (상태 변경은 SSE로 바로 반영)
    refetchInterval: 60_000,
  })
  const users = useQuery({ queryKey: USERS_KEY, queryFn: Api.users })

  return (
    <div className="kb-admin">
      <header className="kb-admin__head">
        <Link to="/" className="kb-admin__brand" aria-label="코레일봇 홈으로">
          <Logo size={30} />
          <b>코레일봇 관리</b>
        </Link>
        <div className="kb-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'reservations'}
            className={tab === 'reservations' ? 'is-active' : ''}
            onClick={() => setTab('reservations')}
          >
            전체 예약
          </button>
          <button
            role="tab"
            aria-selected={tab === 'users'}
            className={tab === 'users' ? 'is-active' : ''}
            onClick={() => setTab('users')}
          >
            사용자
          </button>
        </div>
        <span className="kb-admin__spacer" />
        {reservations.dataUpdatedAt > 0 && (
          <span className="kb-caption">
            관리자 · {kstClock.format(new Date(reservations.dataUpdatedAt))} 기준
          </span>
        )}
      </header>

      {!online && (
        <div className="kb-offline" role="status">
          <Icon name="wifiOff" size={16} /> 오프라인 상태입니다. 연결되면 자동으로 갱신됩니다.
        </div>
      )}

      <main className="kb-admin__main">
        {tab === 'reservations' ? (
          <ReservationsTab query={reservations} users={users.data ?? []} />
        ) : (
          <UsersTab query={users} reservations={reservations.data ?? []} />
        )}
      </main>
    </div>
  )
}

interface QueryState<T> {
  data: T | undefined
  isPending: boolean
  error: unknown
}

function ReservationsTab({ query, users }: { query: QueryState<Reservation[]>; users: AdminUser[] }) {
  const queryClient = useQueryClient()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<Sort>('started')
  const [picked, setPicked] = useState<Set<string>>(new Set())

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['reservations'] })
  const cancel = useMutation({
    mutationFn: (ids: string[]) => Promise.all(ids.map((id) => Api.cancelReservation(id))),
    onSettled: () => {
      setPicked(new Set())
      refresh()
    },
  })
  const cancelAll = useMutation({
    mutationFn: () => Api.cancelAll('all'),
    onSettled: () => {
      setPicked(new Set())
      refresh()
    },
  })

  const names = new Map(users.map((u) => [u.id, u.name]))
  const rows = (query.data ?? []).map((r) => {
    const isAdmin = r.owner_id === 'admin'
    const trainNo = r.status === 'success' ? parseResultText(r.result_text).trainNo : undefined
    return {
      r,
      name: isAdmin ? '관리자' : (names.get(r.owner_id) ?? '-'),
      phone: isAdmin ? '' : displayPhone(r.owner_id),
      via: r.origin === 'web' ? '웹' : '텔레그램',
      train: trainNo ?? (r.train_type === 'KTX' ? 'KTX' : '전체'),
    }
  })

  const term = q.trim()
  const needle = term.toLowerCase()
  const searched = rows.filter(
    (row) =>
      !needle ||
      [row.r.src_station, row.r.dst_station, row.name, row.phone, row.r.owner_id, row.train]
        .join(' ')
        .toLowerCase()
        .includes(needle),
  )
  const filter = FILTERS.find((f) => f.value === status)!
  const list = searched.filter((row) => filter.match(row.r)).sort((a, b) => SORTS[sort](a.r, b.r))
  const pickable = list.filter((row) => row.r.is_active).map((row) => row.r.id)
  const hasActive = rows.some((row) => row.r.is_active)
  // 검색·필터로 가려진 행은 선택돼 있어도 취소하지 않음 (헤더 체크박스와 같은 기준)
  const pickedIds = pickable.filter((id) => picked.has(id))
  const allPicked = pickable.length > 0 && pickable.every((id) => picked.has(id))
  const busy = cancel.isPending || cancelAll.isPending

  const togglePick = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <>
      <div className="kb-pagehead">
        <div>
          <h1>전체 예약</h1>
          <p className="kb-hint">모든 사용자의 예약 작업 · 최근 30일</p>
        </div>
        {pickedIds.length > 0 && (
          <button
            className="kb-btn kb-btn--sm kb-btn--danger-soft"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`선택한 예약 ${pickedIds.length}건을 취소할까요?`)) cancel.mutate(pickedIds)
            }}
          >
            선택한 예약 취소 ({pickedIds.length})
          </button>
        )}
        <button
          className="kb-btn kb-btn--sm kb-btn--danger-soft"
          disabled={busy || !hasActive}
          onClick={() => {
            if (window.confirm('진행 중인 모든 예약을 취소할까요?')) cancelAll.mutate()
          }}
        >
          진행 중 모두 취소
        </button>
      </div>

      <div className="kb-toolbar">
        <div className="kb-input-wrap">
          <Icon name="search" size={18} />
          <input
            className="kb-input"
            type="search"
            placeholder="역, 전화번호, 이름, 열차번호로 검색"
            aria-label="예약 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <div className="kb-filter" role="radiogroup" aria-label="상태">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              role="radio"
              aria-checked={status === f.value}
              className={status === f.value ? 'is-selected' : ''}
              onClick={() => setStatus(f.value)}
            >
              {f.label}
              <small>{searched.filter((row) => f.match(row.r)).length}</small>
            </button>
          ))}
        </div>
        <span className="kb-toolbar__spacer" />
        <select
          className="kb-select"
          aria-label="정렬"
          value={sort}
          onChange={(e) => setSort(e.target.value as Sort)}
        >
          <option value="started">최근 시작순</option>
          <option value="date">출발일 빠른순</option>
          <option value="attempts">시도 많은순</option>
        </select>
      </div>

      <ErrorBox error={cancel.error ?? cancelAll.error ?? query.error} />

      {query.isPending ? (
        <Spinner />
      ) : (
        <div className="kb-table-wrap">
          <div className="kb-grid-table" role="table" aria-label="전체 예약" style={tableStyle(RESERVATION_COLS, 1240)}>
            <div className="kb-grid-row kb-grid-row--head" role="row">
              <div role="columnheader">
                <input
                  type="checkbox"
                  aria-label="보이는 진행 중 예약 모두 선택"
                  checked={allPicked}
                  disabled={pickable.length === 0}
                  onChange={() =>
                    setPicked((prev) => {
                      const next = new Set(prev)
                      pickable.forEach((id) => (allPicked ? next.delete(id) : next.add(id)))
                      return next
                    })
                  }
                />
              </div>
              <div role="columnheader">상태</div>
              <div role="columnheader">구간</div>
              <div role="columnheader">출발일</div>
              <div role="columnheader">시각 범위</div>
              <div role="columnheader">열차 · 좌석</div>
              <div role="columnheader">사용자</div>
              <div role="columnheader" className="is-right">
                시도
              </div>
              <div role="columnheader">시작</div>
              <div role="columnheader">
                <span className="kb-sr-only">동작</span>
              </div>
            </div>

            {list.map(({ r, name, phone, via, train }) => (
              <div
                key={r.id}
                className={`kb-grid-row${r.status === 'cancelled' ? ' is-dim' : ''}`}
                role="row"
              >
                <div role="cell">
                  <input
                    type="checkbox"
                    aria-label={`${r.src_station}→${r.dst_station} 선택`}
                    checked={r.is_active && picked.has(r.id)}
                    disabled={!r.is_active}
                    onChange={() => togglePick(r.id)}
                  />
                </div>
                <div role="cell">
                  <StatusBadge status={r.status} waitlisted={r.waitlisted} />
                </div>
                <div role="cell">
                  <RouteLine
                    size="sm"
                    from={<span>{highlight(r.src_station, term)}</span>}
                    to={<span>{highlight(r.dst_station, term)}</span>}
                    status={r.status}
                  />
                </div>
                <div role="cell">{formatDate(r.dep_date)}</div>
                <div role="cell" className="is-mono">
                  {timeRange(r)}
                </div>
                <div role="cell">
                  <div className="kb-cell-train">
                    <TrainTag reservation={r} label={highlight(train, term)} />
                    <span className="kb-muted">{SEAT_SHORT[r.seat_type]}</span>
                  </div>
                </div>
                <div role="cell">
                  <div className="kb-cell-user">
                    <span>{highlight(name, term)}</span>
                    <span className="is-mono">
                      {phone && <>{highlight(phone, term)} · </>}
                      {via}
                    </span>
                  </div>
                </div>
                <div role="cell" className="is-num">
                  {r.attempts ? r.attempts.toLocaleString() : '-'}
                </div>
                <div role="cell" className="is-sub">
                  {formatDateTime(r.created_at)}
                </div>
                <div role="cell">
                  {r.is_active ? (
                    <button
                      className="kb-btn kb-btn--sm kb-btn--danger-soft"
                      disabled={busy}
                      aria-label={`${r.src_station}→${r.dst_station} ${STATUS_LABELS[r.status]} 예약 취소`}
                      onClick={() => {
                        if (window.confirm('이 예약 작업을 취소할까요?')) cancel.mutate([r.id])
                      }}
                    >
                      취소
                    </button>
                  ) : (
                    <Link to={`/r/${r.id}`} className="kb-btn kb-btn--sm kb-btn--ghost">
                      상세
                    </Link>
                  )}
                </div>
              </div>
            ))}

            {list.length === 0 && (
              <div className="kb-grid-empty">
                {term ? `'${term}'에 해당하는 예약이 없습니다.` : '예약이 없습니다.'}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="kb-tablefoot">
        <span className="kb-caption">{term ? `'${term}' 검색 결과 ${list.length}건` : `${list.length}건`}</span>
        <span className="kb-caption">시도 횟수는 1분마다 갱신됩니다.</span>
      </div>
    </>
  )
}

function UsersTab({ query, reservations }: { query: QueryState<AdminUser[]>; reservations: Reservation[] }) {
  const queryClient = useQueryClient()
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')
  const [uq, setUq] = useState('')

  const refresh = () => queryClient.invalidateQueries({ queryKey: USERS_KEY })
  const create = useMutation({
    mutationFn: () => Api.createUser(phone, name),
    onSuccess: () => {
      setPhone('')
      setName('')
      refresh()
    },
  })
  const update = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      Api.updateUser(id, { is_active }),
    onSuccess: refresh,
  })
  const remove = useMutation({ mutationFn: Api.deleteUser, onSuccess: refresh })

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }

  const term = uq.trim().toLowerCase()
  const digits = term.replace(/\D/g, '')
  const users = (query.data ?? []).filter(
    (u) =>
      !term ||
      `${u.phone} ${u.name ?? ''}`.toLowerCase().includes(term) ||
      (digits.length > 0 && u.id.includes(digits)),
  )

  return (
    <>
      <div className="kb-pagehead">
        <div>
          <h1>사용자</h1>
          <p className="kb-hint">등록된 코레일 계정만 로그인할 수 있습니다.</p>
        </div>
      </div>

      <form className="kb-card kb-add-user" onSubmit={onSubmit}>
        <strong>사용자 추가</strong>
        <input
          className="kb-input kb-input--num"
          type="tel"
          inputMode="numeric"
          placeholder="010-1234-5678"
          aria-label="전화번호"
          value={phone}
          onChange={(e) => setPhone(formatPhoneInput(e.target.value))}
          required
        />
        <input
          className="kb-input"
          placeholder="이름 (선택)"
          aria-label="이름"
          value={name}
          maxLength={50}
          onChange={(e) => setName(e.target.value)}
        />
        <button className="kb-btn kb-btn--primary kb-btn--sm" disabled={create.isPending}>
          {create.isPending ? '추가하는 중…' : '추가'}
        </button>
        <ErrorBox error={create.error} />
      </form>

      <div className="kb-toolbar">
        <div className="kb-input-wrap">
          <Icon name="search" size={18} />
          <input
            className="kb-input"
            type="search"
            placeholder="전화번호, 이름으로 검색"
            aria-label="사용자 검색"
            value={uq}
            onChange={(e) => setUq(e.target.value)}
          />
        </div>
      </div>

      <ErrorBox error={update.error ?? remove.error ?? query.error} />

      {query.isPending ? (
        <Spinner />
      ) : (
        <div className="kb-table-wrap">
          <div className="kb-grid-table" role="table" aria-label="사용자" style={tableStyle(USER_COLS, 960)}>
            <div className="kb-grid-row kb-grid-row--head" role="row">
              <div role="columnheader">전화번호</div>
              <div role="columnheader">이름</div>
              <div role="columnheader">상태</div>
              <div role="columnheader">텔레그램</div>
              <div role="columnheader" className="is-right">
                진행 중
              </div>
              <div role="columnheader" className="is-right">
                30일 예약
              </div>
              <div role="columnheader">최근 로그인</div>
              <div role="columnheader">
                <span className="kb-sr-only">동작</span>
              </div>
            </div>

            {users.map((user) => {
              const mine = reservations.filter((r) => r.owner_id === user.id)
              return (
                <div key={user.id} className={`kb-grid-row${user.is_active ? '' : ' is-dim'}`} role="row">
                  <div role="cell" className="is-mono">
                    {user.phone}
                  </div>
                  <div role="cell">{user.name || '-'}</div>
                  <div role="cell">
                    <span className={`kb-badge kb-badge--${user.is_active ? 'success' : 'cancelled'}`}>
                      {user.is_active ? '활성' : '비활성'}
                    </span>
                  </div>
                  <div role="cell" className="kb-muted">
                    {user.telegram_linked ? (user.telegram_notify ? '연결 · 알림' : '연결') : '-'}
                  </div>
                  <div role="cell" className="is-num">
                    {mine.filter((r) => r.is_active).length}
                  </div>
                  <div role="cell" className="is-num">
                    {mine.length}
                  </div>
                  <div role="cell" className="kb-muted">
                    {formatDateTime(user.last_login_at)}
                  </div>
                  <div role="cell">
                    <div className="kb-cell-actions">
                      <button
                        className="kb-btn kb-btn--sm"
                        disabled={update.isPending}
                        onClick={() => update.mutate({ id: user.id, is_active: !user.is_active })}
                      >
                        {user.is_active ? '비활성화' : '활성화'}
                      </button>
                      <button
                        className="kb-icon-btn kb-icon-btn--danger"
                        aria-label={`${user.phone} 삭제`}
                        disabled={remove.isPending}
                        onClick={() => {
                          if (window.confirm(`${user.phone} 사용자를 삭제할까요?`)) remove.mutate(user.id)
                        }}
                      >
                        <Icon name="trash" size={18} />
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}

            {users.length === 0 && (
              <div className="kb-grid-empty">
                {term ? `'${uq.trim()}'에 해당하는 사용자가 없습니다.` : '등록된 사용자가 없습니다.'}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
