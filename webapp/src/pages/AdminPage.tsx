import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Api, type AdminUser } from '../api'
import { EmptyState, ErrorBox, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { ReservationCard } from '../components/ReservationCard'
import { formatDateTime, formatPhoneInput } from '../format'

type Tab = 'users' | 'reservations'

export function AdminPage() {
  const [tab, setTab] = useState<Tab>('users')
  return (
    <Layout title="관리">
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'users'}
          className={tab === 'users' ? 'active' : ''}
          onClick={() => setTab('users')}
        >
          사용자
        </button>
        <button
          role="tab"
          aria-selected={tab === 'reservations'}
          className={tab === 'reservations' ? 'active' : ''}
          onClick={() => setTab('reservations')}
        >
          전체 예약
        </button>
      </div>
      {tab === 'users' ? <UsersTab /> : <ReservationsTab />}
    </Layout>
  )
}

function UsersTab() {
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['admin', 'users'], queryFn: Api.users })
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
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

  return (
    <>
      <form className="card form add-user" onSubmit={onSubmit}>
        <h2>사용자 추가</h2>
        <div className="row">
          <input
            type="tel"
            inputMode="numeric"
            placeholder="010-1234-5678"
            aria-label="전화번호"
            value={phone}
            onChange={(e) => setPhone(formatPhoneInput(e.target.value))}
            required
          />
          <input
            placeholder="이름 (선택)"
            aria-label="이름"
            value={name}
            maxLength={50}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <ErrorBox error={create.error} />
        <button className="button primary" disabled={create.isPending}>
          <Icon name="plus" size={18} /> 추가
        </button>
      </form>

      <ErrorBox error={update.error ?? remove.error} />

      {users.isPending ? (
        <Spinner />
      ) : users.isError ? (
        <ErrorBox error={users.error} />
      ) : users.data.length === 0 ? (
        <EmptyState title="등록된 사용자가 없습니다" />
      ) : (
        <div className="list">
          {users.data.map((user) => (
            <UserRow
              key={user.id}
              user={user}
              onToggle={() => update.mutate({ id: user.id, is_active: !user.is_active })}
              onDelete={() => {
                if (window.confirm(`${user.phone} 사용자를 삭제할까요?`)) remove.mutate(user.id)
              }}
            />
          ))}
        </div>
      )}
    </>
  )
}

function UserRow({
  user,
  onToggle,
  onDelete,
}: {
  user: AdminUser
  onToggle: () => void
  onDelete: () => void
}) {
  return (
    <div className={`card user-row${user.is_active ? '' : ' inactive'}`}>
      <div className="user-info">
        <strong>{user.phone}</strong>
        <span>{user.name}</span>
        <small className="muted">
          {user.is_active ? '활성' : '비활성'}
          {user.telegram_linked && ' · 텔레그램 연결'}
          {' · 최근 로그인 '}
          {formatDateTime(user.last_login_at)}
        </small>
      </div>
      <div className="user-actions">
        <button className="button small" onClick={onToggle}>
          {user.is_active ? '비활성화' : '활성화'}
        </button>
        <button className="icon-button danger" onClick={onDelete} aria-label="삭제">
          <Icon name="trash" size={18} />
        </button>
      </div>
    </div>
  )
}

function ReservationsTab() {
  const queryClient = useQueryClient()
  const active = useQuery({
    queryKey: ['reservations', 'active', 'all'],
    queryFn: () => Api.reservations('active', 'all'),
    refetchInterval: 15_000,
  })
  const history = useQuery({
    queryKey: ['reservations', 'history', 'all'],
    queryFn: () => Api.reservations('history', 'all'),
  })
  const cancelAll = useMutation({
    mutationFn: () => Api.cancelAll('all'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['reservations'] }),
  })

  return (
    <>
      <section className="section">
        <div className="section-head">
          <h2>진행 중 ({active.data?.length ?? 0})</h2>
          {active.data && active.data.length > 0 && (
            <button
              className="button small danger"
              disabled={cancelAll.isPending}
              onClick={() => {
                if (window.confirm('진행 중인 모든 예약을 취소할까요?')) cancelAll.mutate()
              }}
            >
              모두 취소
            </button>
          )}
        </div>
        <ErrorBox error={cancelAll.error ?? active.error} />
        {active.isPending ? (
          <Spinner />
        ) : active.data?.length ? (
          <div className="list">
            {active.data.map((r) => (
              <ReservationCard key={r.id} reservation={r} showOwner />
            ))}
          </div>
        ) : (
          <p className="muted">진행 중인 예약이 없습니다.</p>
        )}
      </section>
      <section className="section">
        <h2>최근 30일 이력</h2>
        {history.isPending ? (
          <Spinner />
        ) : history.data?.length ? (
          <div className="list">
            {history.data.map((r) => (
              <ReservationCard key={r.id} reservation={r} showOwner />
            ))}
          </div>
        ) : (
          <p className="muted">이력이 없습니다.</p>
        )}
      </section>
    </>
  )
}
