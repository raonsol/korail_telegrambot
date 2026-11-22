import { useQuery } from '@tanstack/react-query'
import { useRef } from 'react'
import { TransitionLink } from '../navigation'
import { Api } from '../api'
import { useAuth } from '../auth'
import { EmptyState, ErrorBox, TicketSkeleton } from '../components/Feedback'
import { AnimatedList, terminalFlash } from '../components/Motion'
import { Icon } from '../components/Icon'
import { Layout } from '../components/Layout'
import { ReservationCard } from '../components/ReservationCard'
import { displayStatus } from '../format'

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

  // 이 화면에서 서버로부터 새로 받은 진행 중 예약 - 이 예약이 끝나 이력으로 옮겨 오면 반응
  // (오래된 캐시에 있던 예약이 다시 조회해 보니 끝나 있던 것은 보는 중에 끝난 것이 아님)
  const seenActive = useRef(new Set<string>())
  if (active.data && active.isFetchedAfterMount) {
    for (const r of active.data) seenActive.current.add(r.id)
  }

  const greeting = me?.user.is_admin ? '관리자' : me?.user.name || me?.user.phone

  return (
    <Layout title="코레일봇" contentClassName="kb-content--fab">
      <p className="kb-greeting">
        안녕하세요, <strong>{greeting}</strong>님
      </p>

      <section className="kb-section kb-section--first">
        <div className="kb-section-head">
          <h2>진행 중인 예약</h2>
          {active.data && active.data.length > 0 && (
            <span className="kb-count">{active.data.length}</span>
          )}
        </div>
        {active.isPending ? (
          <TicketSkeleton label="진행 중인 예약을 불러오는 중" />
        ) : active.isError ? (
          <ErrorBox error={active.error} />
        ) : (
          <>
            {/* 마지막 예약이 끝나도 목록은 남겨 두어 카드가 접히며 사라지게 */}
            <AnimatedList
              items={active.data}
              getKey={(r) => r.id}
              render={(r) => <ReservationCard reservation={r} />}
            />
            {active.data.length === 0 && (
              <EmptyState title="진행 중인 예약이 없습니다">
                <p>원하는 열차 조건을 등록하면 빈 좌석이 나올 때까지 계속 찾아 예약합니다.</p>
                <TransitionLink to="/new" className="kb-btn kb-btn--primary">
                  새 예약 시작
                </TransitionLink>
              </EmptyState>
            )}
          </>
        )}
      </section>

      <section className="kb-section kb-section--near">
        <div className="kb-section-head">
          <h2>최근 30일 이력</h2>
        </div>
        {history.isPending ? (
          <TicketSkeleton count={2} label="이력을 불러오는 중" />
        ) : history.isError ? (
          <ErrorBox error={history.error} />
        ) : history.data.length === 0 ? (
          <p className="kb-muted kb-small">아직 완료된 예약이 없습니다.</p>
        ) : (
          <AnimatedList
            items={history.data}
            getKey={(r) => r.id}
            render={(r, phase) => (
              <ReservationCard
                reservation={r}
                flash={
                  phase === 'in' && seenActive.current.has(r.id)
                    ? terminalFlash(displayStatus(r))
                    : null
                }
              />
            )}
          />
        )}
      </section>

      <TransitionLink to="/new" className="kb-fab kb-fab--float" aria-label="새 예약">
        <Icon name="plus" size={26} />
      </TransitionLink>
    </Layout>
  )
}
