import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Api } from '../api'
import { useAuth } from '../auth'
import { EmptyState, ErrorBox, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { ReservationCard } from '../components/ReservationCard'

export function HomePage() {
  const { me } = useAuth()
  const active = useQuery({
    queryKey: ['reservations', 'active'],
    queryFn: () => Api.reservations('active'),
    // SSE를 쓸 수 없는 환경을 위한 보조 폴링
    refetchInterval: (query) => (query.state.data?.length ? 15_000 : false),
  })
  const history = useQuery({
    queryKey: ['reservations', 'history'],
    queryFn: () => Api.reservations('history'),
  })

  const greeting = me?.user.is_admin ? '관리자' : me?.user.name || me?.user.phone

  return (
    <Layout title="코레일 예약">
      <p className="greeting">
        안녕하세요, <strong>{greeting}</strong>님
      </p>

      <section className="section">
        <div className="section-head">
          <h2>진행 중인 예약</h2>
          {active.data && active.data.length > 0 && (
            <span className="count">{active.data.length}</span>
          )}
        </div>
        {active.isPending ? (
          <Spinner />
        ) : active.isError ? (
          <ErrorBox error={active.error} />
        ) : active.data.length === 0 ? (
          <EmptyState title="진행 중인 예약이 없습니다">
            <p>원하는 열차 조건을 등록하면 빈 좌석이 나올 때까지 계속 찾아 예약합니다.</p>
            <Link to="/new" className="button primary">
              <Icon name="plus" size={18} /> 새 예약 시작
            </Link>
          </EmptyState>
        ) : (
          <div className="list">
            {active.data.map((r) => (
              <ReservationCard key={r.id} reservation={r} />
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h2>최근 30일 이력</h2>
        </div>
        {history.isPending ? (
          <Spinner />
        ) : history.isError ? (
          <ErrorBox error={history.error} />
        ) : history.data.length === 0 ? (
          <p className="muted">아직 완료된 예약이 없습니다.</p>
        ) : (
          <div className="list">
            {history.data.map((r) => (
              <ReservationCard key={r.id} reservation={r} />
            ))}
          </div>
        )}
      </section>

      <Link to="/new" className="fab" aria-label="새 예약">
        <Icon name="plus" size={26} />
      </Link>
    </Layout>
  )
}
