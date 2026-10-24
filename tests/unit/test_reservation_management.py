"""인근역 포함 예약과 예약 후 관리 (코레일 예약 취소, 결제 기한 알림)"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pykorail import AccessRestrictedError, KorailError

from core.db import utcnow
from core.errors import Conflict, KorailUnavailable, NotAllowed, ServiceError
from core.models import Reservation
from core.notifier import ReservationEvent, push_message
from core.schemas import Owner, ReservationOut, WorkerEvent, pay_deadline_utc

USER = Owner(user_id="01012345678")
OTHER = Owner(user_id="01087654321")
KORAIL_ID = "010-1234-5678"


@pytest.fixture
def events(services):
    recorder = AsyncMock()
    services.notifier.add(type("Recorder", (), {"deliver": recorder})())
    return recorder


@pytest.fixture
def korail_cancel(services):
    """코레일 예약 취소 대체: 호출을 기록하고 found 값을 돌려줌"""
    stub = Mock(return_value=True)
    services.reservations.korail_cancel = stub
    return stub


async def _succeeded(services, fake_launcher, request, *, waiting=False, **report):
    """예약을 시작하고 워커가 성공을 보고한 상태로 만듦"""
    r = await services.reservations.start(
        USER, request, KORAIL_ID, "correct", origin="web"
    )
    report.setdefault("korail_rsv_id", "PNR123")
    await services.reservations.handle_worker_event(
        WorkerEvent(
            reservation_id=r.id,
            token=fake_launcher.launched[-1]["callback_token"],
            status="success",
            train_info="[KTX 101] 서울~부산",
            waiting=waiting,
            **report,
        )
    )
    return r.id


def _deadline_kst(minutes):
    """지금부터 minutes 뒤 결제 기한 (워커가 보고하는 KST YYYYMMDDHHMMSS)"""
    from core.schemas import KST

    return (datetime.now(KST) + timedelta(minutes=minutes)).strftime("%Y%m%d%H%M%S")


class TestSchemas:
    def test_pay_deadline_is_stored_as_utc(self):
        assert pay_deadline_utc("20991231101500") == datetime(2099, 12, 31, 1, 15)
        assert pay_deadline_utc(None) is None
        assert pay_deadline_utc("bad") is None

    def test_request_defaults_to_direct_stations(self, valid_request):
        assert valid_request.include_nearby is False


class TestNearby:
    @pytest.mark.asyncio
    async def test_choice_is_stored_and_sent_to_worker(
        self, services, fake_launcher, valid_request
    ):
        request = valid_request.model_copy(update={"include_nearby": True})
        r = await services.reservations.start(
            USER, request, KORAIL_ID, "correct", origin="web"
        )
        assert r.include_nearby is True
        assert fake_launcher.launched[-1]["include_nearby"] is True

    def test_runner_passes_choice_to_handler(self):
        from core.runner import run_reservation

        handler = Mock()
        handler.login.return_value = True
        handler.reserve_single_attempt.return_value = {
            "success": True,
            "result": "KTX",
            "error": None,
        }
        spec = {
            "reservation_id": "r1",
            "korail_id": KORAIL_ID,
            "korail_pw": "pw",
            "dep_date": "20991231",
            "src_station": "용산",
            "dst_station": "대전",
            "dep_time": "0900",
            "max_dep_time": "1200",
            "train_type": "KTX",
            "seat_type": "general",
            "include_nearby": True,
        }
        run_reservation(spec, Mock(), handler_factory=lambda: handler)
        assert handler.reserve_single_attempt.call_args.kwargs["includeNearby"] is True

    @pytest.mark.parametrize("nearby", [True, False])
    def test_search_asks_korail_for_nearby_stations(self, nearby):
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler()
        handler.korail_client = Mock()
        handler.korail_client.trains.search.return_value = []
        handler.reserve_single_attempt(
            "20991231", "용산", "대전", "090000", includeNearby=nearby
        )
        kwargs = handler.korail_client.trains.search.call_args.kwargs
        assert kwargs["include_nearby_stations"] is nearby

    def test_same_train_with_other_stations_is_kept(self):
        """Nearby search returns one train once per station pair; the pair arriving
        on the next page is a new candidate, not a duplicate of the page boundary"""
        from pykorail import NoResultsError

        from telegramBot.korail_client import ReserveHandler

        def train(dep_time, dep_code, seat=False):
            t = Mock(train_no=dep_time, dep_date="20991231", dep_time=dep_time)
            t.dep_code, t.arr_code = dep_code, "0010"
            t.has_seat.return_value = seat
            return t

        early = train("083000", "0001")
        from_seoul = train("090000", "0001")
        again = train("090000", "0001")  # 다음 쪽 경계에 다시 나온 같은 열차
        from_yongsan = train("090000", "0015", seat=True)
        handler = ReserveHandler()
        handler.korail_client = Mock()
        handler.korail_client.trains.search.side_effect = [
            [early, from_seoul],
            [again, from_yongsan],
            NoResultsError(),
        ]
        handler.korail_client.reservations.create.return_value = "reserved"

        result = handler.reserve_single_attempt(
            "20991231", "용산", "대전", "080000", includeNearby=True
        )

        assert result["success"] is True
        created = handler.korail_client.reservations.create.call_args_list
        assert [c.args[0] for c in created] == [from_yongsan]


class TestReservationDetails:
    def test_seat_reservation(self):
        from telegramBot.korail_client import reservation_details

        r = SimpleNamespace(
            rsv_id="PNR1",
            is_waiting=False,
            buy_limit_date="20991231",
            buy_limit_time="101500",
        )
        assert reservation_details(r) == ("PNR1", "20991231101500")

    def test_hhmm_limit_time_is_padded(self):
        from telegramBot.korail_client import reservation_details

        r = SimpleNamespace(
            rsv_id="PNR1",
            is_waiting=False,
            buy_limit_date="20991231",
            buy_limit_time="1015",
        )
        assert reservation_details(r) == ("PNR1", "20991231101500")

    def test_waitlist_has_no_deadline(self):
        from telegramBot.korail_client import reservation_details

        r = SimpleNamespace(
            rsv_id="PNR1",
            is_waiting=True,
            buy_limit_date="00000000",
            buy_limit_time="000000",
        )
        assert reservation_details(r) == ("PNR1", None)

    @pytest.mark.parametrize("result", ["duplicate_reservation", "KTX 101", Mock()])
    def test_unknown_results_have_nothing(self, result):
        from telegramBot.korail_client import reservation_details

        assert reservation_details(result) == (None, None)

    def test_runner_reports_and_records_details(self):
        from core.runner import run_reservation

        reservation = SimpleNamespace(
            rsv_id="PNR1",
            is_waiting=False,
            buy_limit_date="20991231",
            buy_limit_time="101500",
        )
        handler = Mock()
        handler.login.return_value = True
        handler.reserve_single_attempt.return_value = {
            "success": True,
            "result": reservation,
            "error": None,
        }
        reporter = Mock()
        reporter.send.return_value = True
        on_success = Mock()
        spec = {
            "reservation_id": "r1",
            "korail_id": KORAIL_ID,
            "korail_pw": "pw",
            "dep_date": "20991231",
            "src_station": "서울",
            "dst_station": "부산",
            "dep_time": "0900",
            "max_dep_time": "1200",
            "train_type": "KTX",
            "seat_type": "general",
        }

        result = run_reservation(
            spec, reporter, on_success=on_success, handler_factory=lambda: handler
        )

        details = {"korail_rsv_id": "PNR1", "pay_deadline": "20991231101500"}
        assert on_success.call_args.kwargs.items() >= details.items()
        assert reporter.send.call_args.kwargs.items() >= details.items()
        assert result.items() >= details.items()

    def test_result_file_keeps_details(self, tmp_path):
        from core.runner import read_result_file, write_result_file

        path = str(tmp_path / "result.json")
        write_result_file(
            path, "KTX", 3, korail_rsv_id="PNR1", pay_deadline="20991231101500"
        )
        assert read_result_file(path) == {
            "train_info": "KTX",
            "attempts": 3,
            "waiting": False,
            "korail_rsv_id": "PNR1",
            "pay_deadline": "20991231101500",
        }

    @pytest.mark.asyncio
    async def test_recovered_success_keeps_details(
        self, services, fake_launcher, valid_request
    ):
        r = await services.reservations.start(
            USER, valid_request, KORAIL_ID, "correct", origin="web"
        )
        services.reservations.launcher.recover_success = lambda _id: {
            "train_info": "KTX",
            "attempts": 2,
            "korail_rsv_id": "PNR9",
            "pay_deadline": "20991231101500",
        }
        await services.reservations.handle_process_exit(r.id, 1)
        with services.db.session() as s:
            stored = s.get(Reservation, r.id)
            assert stored.status == "success"
            assert stored.korail_rsv_id == "PNR9"
            assert stored.pay_deadline == datetime(2099, 12, 31, 1, 15)


class TestKorailCancel:
    @pytest.mark.asyncio
    async def test_success_report_stores_rsv_id_and_deadline(
        self, services, fake_launcher, valid_request
    ):
        rid = await _succeeded(
            services, fake_launcher, valid_request, pay_deadline="20991231101500"
        )
        r = services.reservations.get(rid, owner=USER)
        assert r.korail_rsv_id == "PNR123"
        assert r.pay_deadline == datetime(2099, 12, 31, 1, 15)
        assert r.korail_cancellable is True

    @pytest.mark.asyncio
    async def test_cancel_uses_account_and_marks_reservation(
        self, services, fake_launcher, valid_request, korail_cancel, events
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        events.reset_mock()

        out = await services.reservations.cancel_korail(
            rid,
            korail_id="01012345678",  # 하이픈 유무와 무관하게 같은 계정
            korail_pw="correct",
            owner=USER,
            source="web",
        )

        assert out.korail_cancelled_at is not None
        assert out.korail_cancellable is False
        args, kwargs = korail_cancel.call_args
        assert args == (KORAIL_ID, "correct", "PNR123")
        assert kwargs["device"] == services.users.korail_device(KORAIL_ID)
        event = events.await_args.args[0]
        assert event.kind == "korail_cancelled"
        assert event.is_terminal_change is False

        # 다시 눌러도 코레일에 또 요청하지 않음
        await services.reservations.cancel_korail(
            rid, korail_id=KORAIL_ID, korail_pw="correct", owner=USER, source="web"
        )
        assert korail_cancel.call_count == 1

    @pytest.mark.asyncio
    async def test_not_in_korail_list(
        self, services, fake_launcher, valid_request, korail_cancel
    ):
        korail_cancel.return_value = False
        rid = await _succeeded(services, fake_launcher, valid_request)
        with pytest.raises(Conflict, match="이미 결제"):
            await services.reservations.cancel_korail(
                rid, korail_id=KORAIL_ID, korail_pw="correct", owner=USER, source="web"
            )
        assert services.reservations.get(rid, owner=USER).korail_cancellable is True

    @pytest.mark.asyncio
    async def test_other_korail_account_is_refused(
        self, services, fake_launcher, valid_request, korail_cancel
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        admin = Owner(user_id="admin", is_admin=True)
        with pytest.raises(NotAllowed, match="코레일 계정"):
            await services.reservations.cancel_korail(
                rid,
                korail_id="admin_user",
                korail_pw="admin_pass",
                owner=admin,
                source="admin",
            )
        korail_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_needs_a_korail_reservation(
        self, services, fake_launcher, valid_request, korail_cancel
    ):
        r = await services.reservations.start(
            USER, valid_request, KORAIL_ID, "correct", origin="web"
        )
        with pytest.raises(Conflict):
            await services.reservations.cancel_korail(
                r.id, korail_id=KORAIL_ID, korail_pw="correct", owner=USER, source="web"
            )
        # "이미 같은 예약이 있음"으로 끝난 성공은 예약번호가 없음
        rid = await _succeeded(
            services, fake_launcher, valid_request, korail_rsv_id=None
        )
        assert services.reservations.get(rid, owner=USER).korail_cancellable is False
        with pytest.raises(Conflict):
            await services.reservations.cancel_korail(
                rid, korail_id=KORAIL_ID, korail_pw="correct", owner=USER, source="web"
            )
        korail_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_user_cannot_see_it(
        self, services, fake_launcher, valid_request, korail_cancel
    ):
        from core.errors import NotFound

        rid = await _succeeded(services, fake_launcher, valid_request)
        with pytest.raises(NotFound):
            await services.reservations.cancel_korail(
                rid, korail_id=KORAIL_ID, korail_pw="x", owner=OTHER, source="web"
            )


class _Handler:
    """default_korail_cancel 용 ReserveHandler 대체"""

    login_ok = True
    loginBlocked = False
    loginPasswordChange = False
    loginError = "비밀번호 오류"
    cancel_result = True
    created = []

    def __init__(self, proxy_url="", egress_id="", *, device=None):
        _Handler.created.append((proxy_url, egress_id, device))
        self.closed = False

    def login(self, *_):
        return self.login_ok

    def cancel_reservation(self, rsv_id):
        if isinstance(self.cancel_result, Exception):
            raise self.cancel_result
        return self.cancel_result

    def close(self):
        pass


class TestDefaultKorailCancel:
    @pytest.fixture(autouse=True)
    def reset(self):
        _Handler.created = []

    def _call(self, handler_cls, pool=None):
        from core.auth import default_korail_cancel

        return default_korail_cancel(
            KORAIL_ID, "pw", "PNR1", pool, handler_cls=handler_cls, device={"d": 1}
        )

    def test_cancels_through_account_egress(self):
        from core.egress import Egress, EgressPool

        pool = EgressPool([Egress("home1", "socks5h://h:1080")])
        assert self._call(_Handler, pool) is True
        assert _Handler.created == [("socks5h://h:1080", "home1", {"d": 1})]

    def test_not_found(self):
        handler = type("H", (_Handler,), {"cancel_result": False})
        assert self._call(handler) is False

    def test_login_failure(self):
        handler = type("H", (_Handler,), {"login_ok": False})
        with pytest.raises(ServiceError, match="로그인에 실패") as exc:
            self._call(handler)
        assert exc.value.code == "KORAIL_LOGIN_FAILED"

    def test_blocked_login_pauses_egress(self):
        from core.egress import Egress, EgressPool

        pool = EgressPool([Egress("home1", "socks5h://h:1080")])
        handler = type("H", (_Handler,), {"login_ok": False, "loginBlocked": True})
        with pytest.raises(KorailUnavailable):
            self._call(handler, pool)
        assert pool.gate("home1").blocked_for() > 0
        # 차단 대기 중에는 요청하지 않음
        with pytest.raises(KorailUnavailable):
            self._call(_Handler, pool)
        assert len(_Handler.created) == 1

    def test_blocked_cancel_pauses_egress(self):
        from core.egress import Egress, EgressPool

        pool = EgressPool([Egress("home1", "socks5h://h:1080")])
        block = AccessRestrictedError(403, "차단", "-2000")
        handler = type("H", (_Handler,), {"cancel_result": block})
        with pytest.raises(KorailUnavailable):
            self._call(handler, pool)
        assert pool.gate("home1").blocked_for() > 0

    def test_korail_refusal(self):
        refusal = KorailError("이미 발권된 예약입니다", "WRR000000")
        handler = type("H", (_Handler,), {"cancel_result": refusal})
        with pytest.raises(ServiceError, match="이미 발권된") as exc:
            self._call(handler)
        assert exc.value.code == "KORAIL_CANCEL_FAILED"


class TestReserveHandlerCancel:
    def test_cancels_found_reservation(self):
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler()
        handler.korail_client = Mock()
        found = Mock()
        handler.korail_client.reservations.find.return_value = found

        assert handler.cancel_reservation("PNR1") is True
        handler.korail_client.reservations.find.assert_called_once_with("PNR1")
        handler.korail_client.reservations.cancel.assert_called_once_with(found)

    def test_missing_reservation(self):
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler()
        handler.korail_client = Mock()
        handler.korail_client.reservations.find.return_value = None

        assert handler.cancel_reservation("PNR1") is False
        handler.korail_client.reservations.cancel.assert_not_called()


class TestPaymentReminder:
    @pytest.mark.asyncio
    async def test_reminds_once_when_deadline_is_near(
        self, services, fake_launcher, valid_request, events
    ):
        near = await _succeeded(
            services, fake_launcher, valid_request, pay_deadline=_deadline_kst(3)
        )
        far = await _succeeded(
            services, fake_launcher, valid_request, pay_deadline=_deadline_kst(15)
        )
        events.reset_mock()

        assert await services.reservations.notify_payment_deadlines() == 1
        event = events.await_args.args[0]
        assert event.kind == "payment_reminder"
        assert event.reservation.id == near
        assert event.is_terminal_change is False

        # 같은 예약은 다시 알리지 않음
        assert await services.reservations.notify_payment_deadlines() == 0
        assert services.reservations.get(far, owner=USER).pay_deadline is not None

    @pytest.mark.asyncio
    async def test_skips_past_waitlisted_and_cancelled(
        self, services, fake_launcher, valid_request, korail_cancel
    ):
        await _succeeded(
            services, fake_launcher, valid_request, pay_deadline=_deadline_kst(-1)
        )
        await _succeeded(
            services,
            fake_launcher,
            valid_request,
            waiting=True,
            pay_deadline=_deadline_kst(3),
        )
        cancelled = await _succeeded(
            services, fake_launcher, valid_request, pay_deadline=_deadline_kst(3)
        )
        await services.reservations.cancel_korail(
            cancelled,
            korail_id=KORAIL_ID,
            korail_pw="correct",
            owner=USER,
            source="web",
        )

        assert await services.reservations.notify_payment_deadlines() == 0

    def test_web_push_message(self):
        out = Mock(
            id="r1",
            src_station="서울",
            dst_station="부산",
            dep_date="20991231",
            result_text="[KTX 101]",
        )
        event = ReservationEvent(out, "success", "system", kind="payment_reminder")
        payload = push_message(event)
        assert "결제 기한" in payload["title"]
        assert payload["url"] == "/app/r/r1"
        # 코레일 취소는 푸시하지 않음 (취소한 사람이 이미 화면을 보고 있음)
        assert (
            push_message(
                ReservationEvent(out, "success", "web", kind="korail_cancelled")
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_housekeeping_loop_checks_deadlines(self, services):
        import asyncio

        from core.services import housekeeping_loop

        services.reservations.notify_payment_deadlines = AsyncMock(return_value=0)
        services.reservations.detect_lost_workers = AsyncMock(return_value=0)
        task = asyncio.create_task(
            housekeeping_loop(services, interval=3600, lost_worker_interval=3600)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        services.reservations.notify_payment_deadlines.assert_awaited()


class TestWebApi:
    @pytest.fixture
    def app(self, services, tmp_path):
        from web.factory import create_app

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>app</html>")
        return create_app(
            services.settings, services, run_housekeeping=False, dist_dir=str(dist)
        )

    @pytest.mark.asyncio
    async def test_korail_cancel_uses_session_credentials(
        self, app, services, fake_launcher, valid_request, korail_cancel
    ):
        import httpx

        rid = await _succeeded(services, fake_launcher, valid_request)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            login = await client.post(
                "/api/auth/login", json={"phone": KORAIL_ID, "password": "correct"}
            )
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

            response = await client.post(f"/api/reservations/{rid}/korail-cancel")

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["korail_cancelled_at"] is not None
            assert body["korail_cancellable"] is False
            assert korail_cancel.call_args.args[:2] == (KORAIL_ID, "correct")

            korail_cancel.return_value = False
            other = await _succeeded(services, fake_launcher, valid_request)
            response = await client.post(f"/api/reservations/{other}/korail-cancel")
            assert response.status_code == 409
            assert "이미 결제" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_requires_csrf(
        self, app, services, fake_launcher, valid_request, korail_cancel
    ):
        import httpx

        rid = await _succeeded(services, fake_launcher, valid_request)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.post(
                "/api/auth/login", json={"phone": KORAIL_ID, "password": "correct"}
            )
            response = await client.post(f"/api/reservations/{rid}/korail-cancel")
            assert response.status_code == 403
        korail_cancel.assert_not_called()


class TestTelegram:
    @pytest.fixture
    def bot(self, services):
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
        bot.send_message = AsyncMock()
        return bot

    def _confirm_state(self, bot, chat_id=1):
        bot.userDict[chat_id] = {
            "lastAction": 11,
            "userInfo": {"korailId": KORAIL_ID, "korailPw": "correct"},
            "trainInfo": {
                "depDate": "20991231",
                "srcLocate": "용산",
                "dstLocate": "대전",
                "depTime": "0900",
                "maxDepTime": "1200",
                "trainType": "KTX",
                "trainTypeShow": "KTX",
                "specialInfo": "general",
                "specialInfoShow": "일반실 우선 예약",
            },
        }

    @pytest.mark.asyncio
    async def test_confirm_screen_toggles_nearby(self, bot):
        self._confirm_state(bot)
        await bot._send_confirm_reserve(1)
        text = bot.send_message.call_args.kwargs["text"]
        assert "인근역 : 포함 안 함" in text

        await bot._toggle_nearby(1)
        assert bot.userDict[1]["trainInfo"]["includeNearby"] is True
        text = bot.send_message.call_args.kwargs["text"]
        assert "인근역 : 포함" in text and "포함 안 함" not in text

        # 예약이 시작된 뒤에는 이전 확인 화면의 버튼을 무시
        bot.userDict[1]["lastAction"] = 12
        await bot._toggle_nearby(1)
        assert bot.userDict[1]["trainInfo"]["includeNearby"] is True

    @pytest.mark.asyncio
    async def test_success_message_has_korail_cancel_button(
        self, bot, services, fake_launcher, valid_request
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        out = services.reservations.get(rid, owner=USER)
        await bot.deliver(ReservationEvent(out, "running", "worker", chat_id=1))

        markup = bot.send_message.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == f"kcancel_{rid}"

    @pytest.mark.asyncio
    async def test_nearby_success_message_notes_stations(
        self, bot, services, fake_launcher, valid_request
    ):
        request = valid_request.model_copy(update={"include_nearby": True})
        rid = await _succeeded(services, fake_launcher, request)
        out = services.reservations.get(rid, owner=USER)
        await bot.deliver(ReservationEvent(out, "running", "worker", chat_id=1))
        assert "인근역 포함 예약" in bot.send_message.call_args.args[1]

    @pytest.mark.asyncio
    async def test_payment_reminder_message(
        self, bot, services, fake_launcher, valid_request
    ):
        rid = await _succeeded(
            services, fake_launcher, valid_request, pay_deadline="20991231101500"
        )
        out = services.reservations.get(rid, owner=USER)
        await bot.deliver(
            ReservationEvent(
                out, "success", "system", chat_id=1, kind="payment_reminder"
            )
        )
        msg = bot.send_message.call_args.args[1]
        assert "결제 기한" in msg
        assert "10:15까지" in msg  # KST

    @pytest.mark.asyncio
    async def test_korail_cancel_asks_then_cancels(
        self, bot, services, fake_launcher, valid_request, korail_cancel
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        self._confirm_state(bot)
        bot.userDict[1]["userInfo"]["ownerId"] = USER.user_id

        await bot._handle_korail_cancel_callback(1, f"kcancel_{rid}")
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == f"kcancel_yes_{rid}"
        korail_cancel.assert_not_called()

        await bot._handle_korail_cancel_callback(1, f"kcancel_yes_{rid}")
        assert korail_cancel.call_args.args == (KORAIL_ID, "correct", "PNR123")
        assert "취소했습니다" in bot.send_message.call_args.args[1]
        assert services.reservations.get(rid, owner=USER).korail_cancelled_at

    @pytest.mark.asyncio
    async def test_korail_cancel_without_login(
        self, bot, services, fake_launcher, valid_request, korail_cancel
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        bot.ensure_user_exists(1)

        await bot._handle_korail_cancel_callback(1, f"kcancel_yes_{rid}")

        assert "/start" in bot.send_message.call_args.args[1]
        korail_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_korail_cancel_reports_service_errors(
        self, bot, services, fake_launcher, valid_request, korail_cancel
    ):
        korail_cancel.return_value = False
        rid = await _succeeded(services, fake_launcher, valid_request)
        self._confirm_state(bot)
        bot.userDict[1]["userInfo"]["ownerId"] = USER.user_id

        await bot._handle_korail_cancel_callback(1, f"kcancel_yes_{rid}")

        assert "이미 결제" in bot.send_message.call_args.args[1]

    @pytest.mark.asyncio
    async def test_web_cancel_is_announced_on_telegram(
        self, bot, services, fake_launcher, valid_request
    ):
        rid = await _succeeded(services, fake_launcher, valid_request)
        out = services.reservations.get(rid, owner=USER)
        await bot.deliver(
            ReservationEvent(out, "success", "web", chat_id=1, kind="korail_cancelled")
        )
        assert "코레일 예약을 취소했습니다" in bot.send_message.call_args.args[1]

        bot.send_message.reset_mock()
        await bot.deliver(
            ReservationEvent(
                out, "success", "telegram", chat_id=1, kind="korail_cancelled"
            )
        )
        bot.send_message.assert_not_called()
