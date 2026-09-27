"""ReservationService: 시작/취소/워커 보고/정리 작업"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from core.db import utcnow
from core.errors import LimitExceeded, NotAllowed, NotFound, ServiceError
from core.models import Reservation
from core.schemas import Owner, WorkerEvent

USER = Owner(user_id="01012345678")
OTHER = Owner(user_id="01087654321")
ADMIN = Owner(user_id="admin", is_admin=True)


async def _start(services, owner=USER, request=None, **kwargs):
    kwargs.setdefault("origin", "web")
    return await services.reservations.start(owner, request, "010", "pw", **kwargs)


def _token(fake_launcher, index=-1):
    return fake_launcher.launched[index]["callback_token"]


@pytest.fixture
def events(services):
    recorder = AsyncMock()
    services.notifier.add(type("Recorder", (), {"deliver": recorder})())
    return recorder


class TestStart:
    @pytest.mark.asyncio
    async def test_start_creates_queued_reservation(
        self, services, fake_launcher, valid_request, events
    ):
        r = await _start(services, request=valid_request, chat_id=None)

        assert r.status.value == "queued"
        assert r.is_active
        assert r.owner_id == USER.user_id
        spec = fake_launcher.launched[0]
        assert spec["reservation_id"] == r.id
        assert spec["callback_url"] == "http://testserver/internal/events"
        assert spec["korail_pw"] == "pw"
        assert spec["dep_date"] == valid_request.dep_date_compact
        # 워커 루프가 지키는 최대 실행 시간 (RESERVATION_TIMEOUT)
        assert spec["max_duration"] == services.settings.reservation_timeout
        # 토큰 원문은 DB에 저장하지 않음
        with services.db.session() as s:
            stored = s.get(Reservation, r.id)
            assert stored.callback_token_hash != spec["callback_token"]
            assert stored.runner_ref == "ref-1"
        events.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_per_user_limit(self, services, valid_request):
        for _ in range(3):
            await _start(services, request=valid_request)
        with pytest.raises(LimitExceeded, match="최대 3개"):
            await _start(services, request=valid_request)
        # 다른 사용자와 관리자는 영향 없음
        await _start(services, OTHER, request=valid_request)
        for _ in range(4):
            await _start(services, ADMIN, request=valid_request)

    @pytest.mark.asyncio
    async def test_global_limit(self, services, valid_request):
        services.reservations.max_active_total = 2
        await _start(services, request=valid_request)
        await _start(services, OTHER, request=valid_request)
        with pytest.raises(LimitExceeded, match="너무 많습니다"):
            await _start(services, ADMIN, request=valid_request)

    @pytest.mark.asyncio
    async def test_launch_failure_marks_error(
        self, services, fake_launcher, valid_request
    ):
        fake_launcher.fail = True
        with pytest.raises(ServiceError):
            await _start(services, request=valid_request)
        [r] = services.reservations.list(USER)
        assert r.status.value == "error"


class TestStartConcurrency:
    @pytest.mark.asyncio
    async def test_start_does_not_block_event_loop(
        self, services, fake_launcher, valid_request
    ):
        """프로세스 실행(Popen)·DB 기록은 스레드에서 수행 → 다른 요청이 멈추지 않음"""
        import asyncio
        import time

        original = fake_launcher.launch

        def slow_launch(spec):
            time.sleep(0.3)
            return original(spec)

        fake_launcher.launch = slow_launch
        lags = []

        async def ticker():
            for _ in range(10):
                t = time.perf_counter()
                await asyncio.sleep(0.02)
                lags.append(time.perf_counter() - t - 0.02)

        # 틱 측정을 먼저 시작해야 예약 시작이 루프를 막는지 잡아낼 수 있음
        ticking = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        await _start(services, request=valid_request)
        await ticking
        assert max(lags) < 0.2

    @pytest.mark.asyncio
    async def test_concurrent_starts_respect_per_user_limit(
        self, services, fake_launcher, valid_request
    ):
        """한도 확인과 기록이 원자적 → 동시 요청이 몰려도 한도를 넘지 않음"""
        import asyncio
        import time

        original = fake_launcher.launch

        def slow_launch(spec):
            time.sleep(0.05)
            return original(spec)

        fake_launcher.launch = slow_launch
        results = await asyncio.gather(
            *[_start(services, request=valid_request) for _ in range(10)],
            return_exceptions=True,
        )
        started = [r for r in results if not isinstance(r, Exception)]
        rejected = [r for r in results if isinstance(r, LimitExceeded)]
        assert len(started) == 3
        assert len(rejected) == 7
        assert services.reservations.count_active(USER.user_id) == 3


class TestAccess:
    @pytest.mark.asyncio
    async def test_list_and_get_scoping(self, services, valid_request):
        mine = await _start(services, request=valid_request, chat_id=1)
        theirs = await _start(services, OTHER, request=valid_request, chat_id=2)

        assert [r.id for r in services.reservations.list(USER)] == [mine.id]
        assert [r.id for r in services.reservations.list(None, chat_id=2)] == [
            theirs.id
        ]
        assert services.reservations.list(None) == []
        with pytest.raises(NotFound):
            services.reservations.get(theirs.id, USER)
        assert services.reservations.get(theirs.id, ADMIN).id == theirs.id
        assert {r.id for r in services.reservations.list(ADMIN, scope_all=True)} == {
            mine.id,
            theirs.id,
        }
        with pytest.raises(NotAllowed):
            services.reservations.list(USER, scope_all=True)

    @pytest.mark.asyncio
    async def test_active_and_history_filters(
        self, services, fake_launcher, valid_request
    ):
        running = await _start(services, request=valid_request)
        done = await _start(services, request=valid_request)
        await services.reservations.cancel(done.id, owner=USER, source="web")

        active = services.reservations.list(USER, active=True)
        history = services.reservations.list(USER, active=False)
        assert [r.id for r in active] == [running.id]
        assert [r.id for r in history] == [done.id]


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_stops_runner(
        self, services, fake_launcher, valid_request, events
    ):
        r = await _start(services, request=valid_request)

        cancelled = await services.reservations.cancel(r.id, owner=USER, source="web")

        assert cancelled.status.value == "cancelled"
        assert cancelled.finished_at is not None
        assert fake_launcher.cancelled == ["ref-1"]
        event = events.await_args_list[-1].args[0]
        assert event.source == "web" and event.is_terminal_change

    @pytest.mark.asyncio
    async def test_cancel_other_users_reservation(self, services, valid_request):
        r = await _start(services, OTHER, request=valid_request)
        with pytest.raises(NotFound):
            await services.reservations.cancel(r.id, owner=USER, source="web")

    @pytest.mark.asyncio
    async def test_cancel_finished_is_noop(
        self, services, fake_launcher, valid_request
    ):
        r = await _start(services, request=valid_request)
        await services.reservations.cancel(r.id, owner=USER, source="web")
        again = await services.reservations.cancel(r.id, owner=USER, source="web")
        assert again.status.value == "cancelled"
        assert fake_launcher.cancelled == ["ref-1"]

    @pytest.mark.asyncio
    async def test_cancel_many_scope_all(self, services, valid_request):
        await _start(services, request=valid_request)
        await _start(services, OTHER, request=valid_request)

        cancelled = await services.reservations.cancel_many(
            owner=ADMIN, scope_all=True, source="admin"
        )

        assert len(cancelled) == 2
        assert services.reservations.count_active() == 0


class TestWorkerEvents:
    @pytest.mark.asyncio
    async def test_status_progression(self, services, fake_launcher, valid_request):
        r = await _start(services, request=valid_request)
        token = _token(fake_launcher)
        handle = services.reservations.handle_worker_event

        assert await handle(
            WorkerEvent(reservation_id=r.id, token=token, status="running")
        )
        assert services.reservations.get(r.id, USER).status.value == "running"

        await handle(
            WorkerEvent(
                reservation_id=r.id, token=token, status="progress", attempts=50
            )
        )
        # 늦게 도착한 작은 시도 횟수로 되돌아가지 않음
        await handle(
            WorkerEvent(
                reservation_id=r.id, token=token, status="progress", attempts=10
            )
        )
        assert services.reservations.get(r.id, USER).attempts == 50

        await handle(
            WorkerEvent(
                reservation_id=r.id,
                token=token,
                status="failed",
                attempts=1000,
                message="최대 시도 횟수 초과",
            )
        )
        final = services.reservations.get(r.id, USER)
        assert final.status.value == "failed"
        assert final.error == "최대 시도 횟수 초과"
        assert final.finished_at is not None
        assert not await handle(
            WorkerEvent(reservation_id=r.id, token=token, status="success")
        )

    @pytest.mark.asyncio
    async def test_invalid_token(self, services, valid_request):
        r = await _start(services, request=valid_request)
        with pytest.raises(NotAllowed):
            await services.reservations.handle_worker_event(
                WorkerEvent(reservation_id=r.id, token="forged", status="success")
            )

    @pytest.mark.asyncio
    async def test_unknown_reservation(self, services):
        with pytest.raises(NotFound):
            await services.reservations.handle_worker_event(
                WorkerEvent(reservation_id="missing", token="x", status="success")
            )

    @pytest.mark.asyncio
    async def test_process_exit_after_success_is_ignored(
        self, services, fake_launcher, valid_request
    ):
        r = await _start(services, request=valid_request)
        await services.reservations.handle_worker_event(
            WorkerEvent(
                reservation_id=r.id,
                token=_token(fake_launcher),
                status="success",
                train_info="KTX",
            )
        )
        await services.reservations.handle_process_exit(r.id, 0)
        assert services.reservations.get(r.id, USER).status.value == "success"


class TestHousekeeping:
    def _age(self, services, reservation_id, **fields):
        with services.db.session() as s:
            r = s.get(Reservation, reservation_id)
            for key, value in fields.items():
                setattr(r, key, value)

    @pytest.mark.asyncio
    async def test_expire_stale_running_and_queued(
        self, services, fake_launcher, valid_request
    ):
        idle = await _start(services, request=valid_request)
        fresh = await _start(services, request=valid_request)
        stuck = await _start(services, OTHER, request=valid_request)
        now = utcnow()
        self._age(
            services, idle.id, status="running", updated_at=now - timedelta(hours=1)
        )
        self._age(services, fresh.id, status="running", updated_at=now)
        self._age(services, stuck.id, created_at=now - timedelta(days=2))

        assert await services.reservations.expire_stale() == 2

        assert services.reservations.get(idle.id, USER).status.value == "error"
        assert services.reservations.get(fresh.id, USER).is_active
        assert services.reservations.get(stuck.id, OTHER).status.value == "error"
        assert set(fake_launcher.cancelled) == {"ref-1", "ref-3"}

    @pytest.mark.asyncio
    async def test_abort_interrupted_subprocess_reservations(
        self, services, fake_launcher, valid_request, events
    ):
        """subprocess 워커는 웹 서버와 함께 종료되므로 시작 시 남은 예약을 오류 처리"""
        from core.reservations import INTERRUPTED_MESSAGE

        queued = await _start(services, request=valid_request)
        running = await _start(services, OTHER, request=valid_request)
        celery = await _start(services, request=valid_request)
        done = await _start(services, request=valid_request)
        self._age(services, running.id, status="running")
        self._age(services, celery.id, status="running", runner="celery")
        self._age(services, done.id, status="success")
        events.reset_mock()

        assert await services.reservations.abort_interrupted() == 2

        for r, owner in ((queued, USER), (running, OTHER)):
            out = services.reservations.get(r.id, owner)
            assert out.status.value == "error"
            assert out.error == INTERRUPTED_MESSAGE
        # Celery 워커는 웹 서버와 별개로 계속 실행됨
        assert services.reservations.get(celery.id, USER).status.value == "running"
        assert services.reservations.get(done.id, USER).status.value == "success"
        assert events.await_count == 2
        # 이미 종료된 워커이므로 프로세스 종료를 시도하지 않음
        assert fake_launcher.cancelled == []
        assert await services.reservations.abort_interrupted() == 0

    @pytest.mark.asyncio
    async def test_purge_history_after_30_days(self, services, valid_request):
        old = await _start(services, request=valid_request)
        recent = await _start(services, request=valid_request)
        running = await _start(services, request=valid_request)
        now = utcnow()
        self._age(
            services,
            old.id,
            status="success",
            finished_at=now - timedelta(days=31),
            created_at=now - timedelta(days=31),
        )
        self._age(
            services, recent.id, status="failed", finished_at=now - timedelta(days=29)
        )
        self._age(services, running.id, created_at=now - timedelta(days=40))

        assert services.reservations.retention_days == 30
        assert services.reservations.purge_history() == 1

        ids = {r.id for r in services.reservations.list(USER)}
        assert ids == {recent.id, running.id}
