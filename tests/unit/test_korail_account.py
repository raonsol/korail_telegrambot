"""내 코레일 예약·승차권 (웹 "내 승차권", 텔레그램 /mytickets)"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pykorail import AccessRestrictedError, KorailError

from core.egress import Egress, EgressPool, MemoryGate
from core.errors import (
    Conflict,
    KorailPasswordChangeRequired,
    KorailUnavailable,
    ServiceError,
)
from core.korail_account import KorailAccountService
from core.schemas import Owner, WorkerEvent

KORAIL_ID = "010-1234-5678"
USER = Owner(user_id="01012345678")


def _train(
    no="00403", dep="서울", arr="서대전", date="20991031", t1="091700", t2="102400"
):
    return SimpleNamespace(
        train_no=no,
        train_type_name="KTX",
        dep_date=date,
        dep_time=t1,
        arr_time=t2,
        dep_name=dep,
        arr_name=arr,
    )


def _reservation(rsv_id="PNR1", waiting=False, **train):
    return SimpleNamespace(
        rsv_id=rsv_id,
        train=_train(**train),
        price=23700,
        seat_no_count=1,
        is_waiting=waiting,
        buy_limit_date="00000000" if waiting else "20991031",
        buy_limit_time="000000" if waiting else "232400",
    )


def _ticket(pnr="PNR9", seat_end=None, count=1):
    return SimpleNamespace(
        pnr_no=pnr,
        train=_train(no="00101", arr="부산", date="20991105", t1="090000", t2="113000"),
        car_no="3",
        seat_no="12A",
        seat_no_end=seat_end,
        seat_no_count=count,
        price=59800,
    )


class FakeHandler:
    """ReserveHandler 대체 (로그인 결과와 코레일 목록을 정해 둠)"""

    instances = []

    def __init__(self, proxy_url="", egress_id="", *, device=None, **behaviour):
        self.proxy_url, self.egress_id, self.device = proxy_url, egress_id, device
        self.login_ok = behaviour.get("login_ok", True)
        self.loginBlocked = behaviour.get("blocked", False)
        self.loginPasswordChange = behaviour.get("password_change", False)
        self.loginRejected = behaviour.get("rejected", False)
        self.loginError = behaviour.get("error", "비밀번호 오류")
        self.korail_client = Mock()
        self.korail_client.reservations.all.side_effect = behaviour.get(
            "reservations", lambda: [_reservation()]
        )
        self.korail_client.tickets.all.side_effect = behaviour.get(
            "tickets", lambda: [_ticket()]
        )
        self.closed = False
        FakeHandler.instances.append(self)

    def login(self, korail_id, password):
        self.credentials = (korail_id, password)
        return self.login_ok

    def close(self):
        self.closed = True


def _fake_pace(client, before):
    """pykorail 요청 흉내: 목록 요청 1번 + 항목마다 상세 요청 1번씩 before() 를 거침"""
    for listing in (client.reservations.all, client.tickets.all):
        fetch = listing.side_effect

        def paced(_fetch=fetch):
            before()
            items = _fetch()
            for _ in items:
                before()
            return items

        listing.side_effect = paced


def _service(pool=None, links=None, clock=None, **behaviour):
    FakeHandler.instances = []
    pool = pool or EgressPool([Egress("home1", "socks5h://h:1080")])
    service = KorailAccountService(
        pool,
        device_for=lambda _id: {"android_id": "a" * 16},
        links_for=lambda _id, rsv_ids: {
            k: v for k, v in (links or {}).items() if k in rsv_ids
        },
        handler_factory=lambda *a, **k: FakeHandler(*a, **k, **behaviour),
        sleep=Mock(),
        pace=_fake_pace,
        **({"clock": clock} if clock else {}),
    )
    return service, pool


class TestOverview:
    def test_lists_pending_and_issued(self):
        service, _ = _service(links={"PNR1": "bot-r1"})

        out = service.overview(KORAIL_ID, "pw")

        handler = FakeHandler.instances[0]
        assert (handler.proxy_url, handler.egress_id) == ("socks5h://h:1080", "home1")
        assert handler.device == {"android_id": "a" * 16}
        assert handler.credentials == (KORAIL_ID, "pw")
        assert handler.closed
        r = out.reservations[0]
        assert (r.rsv_id, r.train_no, r.dep_station, r.arr_station) == (
            "PNR1",
            "00403",
            "서울",
            "서대전",
        )
        assert (r.dep_time, r.arr_time, r.price, r.seat_count) == (
            "0917",
            "1024",
            23700,
            1,
        )
        assert r.waiting is False
        assert r.pay_deadline == datetime(2099, 10, 31, 14, 24)  # KST 23:24 -> UTC
        assert r.reservation_id == "bot-r1"
        t = out.tickets[0]
        assert (t.rsv_id, t.car_no, t.seats, t.price) == ("PNR9", "3", "12A", 59800)
        assert t.reservation_id is None

    def test_does_not_expose_ticket_secrets(self):
        service, _ = _service()
        body = service.overview(KORAIL_ID, "pw").model_dump()
        assert "ticket_no" not in body["tickets"][0]
        assert "sale_info4" not in str(body)

    def test_waitlist_and_seat_range(self):
        service, _ = _service(
            reservations=lambda: [_reservation(waiting=True)],
            tickets=lambda: [_ticket(seat_end="12B", count=2)],
        )
        out = service.overview(KORAIL_ID, "pw")
        assert out.reservations[0].waiting is True
        assert out.reservations[0].pay_deadline is None
        assert out.tickets[0].seats == "12A~12B"

    def test_caches_per_account_until_refresh(self):
        service, _ = _service()
        first = service.overview(KORAIL_ID, "pw")
        # 하이픈 없는 같은 계정도 같은 캐시
        assert service.overview("01012345678", "pw") is first
        assert len(FakeHandler.instances) == 1

        service.overview(KORAIL_ID, "pw", refresh=True)
        assert len(FakeHandler.instances) == 2

        service.forget(KORAIL_ID)
        assert service.cached(KORAIL_ID) is None

    def test_without_drops_cancelled_reservation(self):
        service, _ = _service(
            reservations=lambda: [_reservation("PNR1"), _reservation("PNR2")]
        )
        out = service.overview(KORAIL_ID, "pw")
        assert [r.rsv_id for r in service.without(out, "PNR1").reservations] == ["PNR2"]
        assert len(out.reservations) == 2

    def test_each_korail_call_takes_an_egress_slot(self):
        gate = Mock(**{"blocked_for.return_value": 0, "reserve_slot.return_value": 0})
        pool = EgressPool([Egress("home1", "socks5h://h:1080")])
        pool._gates["home1"] = gate
        service, _ = _service(pool=pool)
        service.overview(KORAIL_ID, "pw")
        # 로그인 + (예약 목록 + 예약 1건 상세) + (승차권 목록 + 승차권 1장 상세)
        assert gate.reserve_slot.call_count == 5

    def test_paces_every_http_request_of_the_client(self):
        from curl_cffi import requests as curl_requests

        from core.korail_account import _pace_korail_requests

        session = curl_requests.Session()
        sent = []
        session.get = lambda url, **kw: sent.append(("GET", url)) or "r"
        session.post = lambda url, **kw: sent.append(("POST", url)) or "r"
        client = SimpleNamespace(
            _api=SimpleNamespace(_session=SimpleNamespace(_session=session))
        )
        before = Mock(side_effect=lambda: sent.append("slot"))

        _pace_korail_requests(client, before)

        assert session.get("u1", params={}) == "r"
        session.post("u2", data={})
        assert sent == ["slot", ("GET", "u1"), "slot", ("POST", "u2")]
        session.close()

    def test_cancel_during_fetch_is_not_undone_by_cache(self):
        service = None

        def cancelled_meanwhile():
            service.forget(KORAIL_ID)  # 다른 채널에서 그 사이 취소
            return [_reservation("PNR1")]

        service, _ = _service(reservations=cancelled_meanwhile)
        out = service.overview(KORAIL_ID, "pw")
        assert out.reservations[0].rsv_id == "PNR1"
        assert service.cached(KORAIL_ID) is None

    def test_cache_keeps_others_when_refreshing_and_drops_expired(self):
        from core import korail_account

        now = [0.0]
        service, _ = _service(clock=lambda: now[0])
        with patch.object(korail_account, "CACHE_MAX_ENTRIES", 2):
            service.overview("010-0000-0001", "pw")
            service.overview("010-0000-0002", "pw")
            service.overview("010-0000-0002", "pw", refresh=True)
            assert service.cached("010-0000-0001") is not None
            now[0] = korail_account.CACHE_TTL_SECONDS + 1
            service.overview("010-0000-0003", "pw")
            assert set(service._cache) == {"01000000003"}

    def test_busy_egress(self):
        gate = Mock(**{"blocked_for.return_value": 0, "reserve_slot.return_value": 30})
        pool = EgressPool([Egress("home1", "socks5h://h:1080")])
        pool._gates["home1"] = gate
        service, _ = _service(pool=pool)
        with pytest.raises(ServiceError) as exc:
            service.overview(KORAIL_ID, "pw")
        assert exc.value.code == "KORAIL_ACCOUNT_BUSY"


class TestErrors:
    def test_wrong_password(self):
        service, _ = _service(login_ok=False)
        with pytest.raises(ServiceError) as exc:
            service.overview(KORAIL_ID, "pw")
        assert exc.value.code == "KORAIL_LOGIN_FAILED"
        assert FakeHandler.instances[0].closed

    def test_rejected_password_is_not_retried_for_a_while(self):
        now = [0.0]
        service, _ = _service(login_ok=False, rejected=True, clock=lambda: now[0])
        with pytest.raises(ServiceError):
            service.overview(KORAIL_ID, "old-pw")
        # 새로고침해도 같은 비밀번호로는 코레일에 다시 로그인하지 않음 (계정 잠김 방지)
        with pytest.raises(ServiceError, match="10분") as exc:
            service.overview("01012345678", "old-pw", refresh=True)
        assert exc.value.code == "KORAIL_LOGIN_FAILED"
        assert len(FakeHandler.instances) == 1
        # 다른 비밀번호, 또는 시간이 지나면 다시 시도
        with pytest.raises(ServiceError):
            service.overview(KORAIL_ID, "new-pw")
        now[0] = 601
        with pytest.raises(ServiceError):
            service.overview(KORAIL_ID, "old-pw")
        assert len(FakeHandler.instances) == 3

    def test_other_login_failures_are_retried(self):
        service, _ = _service(
            login_ok=False, error="코레일 서버에 연결하지 못했습니다."
        )
        for _ in range(2):
            with pytest.raises(ServiceError):
                service.overview(KORAIL_ID, "pw")
        assert len(FakeHandler.instances) == 2

    def test_blocked_login_pauses_egress(self):
        service, pool = _service(login_ok=False, blocked=True, error="차단")
        with pytest.raises(KorailUnavailable):
            service.overview(KORAIL_ID, "pw")
        assert pool.gate("home1").blocked_for() > 0
        # 차단 대기 중에는 요청하지 않음
        with pytest.raises(KorailUnavailable):
            service.overview(KORAIL_ID, "pw")
        assert len(FakeHandler.instances) == 2  # 두 번째는 로그인 전에 멈춤

    def test_password_change_notice(self):
        service, _ = _service(login_ok=False, password_change=True)
        with pytest.raises(KorailPasswordChangeRequired):
            service.overview(KORAIL_ID, "pw")

    def test_app_outdated_at_login(self):
        from telegramBot.korail_client import APP_OUTDATED_MESSAGE

        service, _ = _service(login_ok=False, error=APP_OUTDATED_MESSAGE)
        with pytest.raises(KorailUnavailable) as exc:
            service.overview(KORAIL_ID, "pw")
        assert exc.value.code == "KORAIL_APP_OUTDATED"

    def test_app_outdated_listing(self):
        def outdated():
            raise KorailError("최신 버전으로 업데이트해 주세요.", "-8204")

        service, _ = _service(reservations=outdated)
        with pytest.raises(KorailUnavailable) as exc:
            service.overview(KORAIL_ID, "pw")
        assert exc.value.code == "KORAIL_APP_OUTDATED"

    def test_block_while_listing(self):
        def blocked():
            raise AccessRestrictedError(403, "차단", "-2000")

        service, pool = _service(tickets=blocked)
        with pytest.raises(KorailUnavailable):
            service.overview(KORAIL_ID, "pw")
        assert pool.gate("home1").blocked_for() > 0
        assert service.cached(KORAIL_ID) is None

    def test_other_failures(self):
        def broken():
            raise ConnectionError("proxy down")

        service, _ = _service(reservations=broken)
        with pytest.raises(ServiceError) as exc:
            service.overview(KORAIL_ID, "pw")
        assert exc.value.code == "KORAIL_ACCOUNT_FAILED"


@pytest.fixture
def korail_cancel(services):
    stub = Mock(return_value=True)
    services.reservations.korail_cancel = stub
    return stub


@pytest.fixture
def events(services):
    recorder = AsyncMock()
    services.notifier.add(type("Recorder", (), {"deliver": recorder})())
    return recorder


async def _bot_reservation(
    services, fake_launcher, request, korail_id=KORAIL_ID, rsv="PNR1"
):
    """코레일봇이 잡은 예약 (예약번호 rsv)"""
    r = await services.reservations.start(USER, request, korail_id, "pw", origin="web")
    await services.reservations.handle_worker_event(
        WorkerEvent(
            reservation_id=r.id,
            token=fake_launcher.launched[-1]["callback_token"],
            status="success",
            train_info="KTX",
            korail_rsv_id=rsv,
        )
    )
    return r.id


class TestCancelByRsv:
    @pytest.mark.asyncio
    async def test_cancels_direct_reservation(self, services, korail_cancel, events):
        events.reset_mock()
        await services.reservations.cancel_korail_rsv(
            KORAIL_ID, "pw", "PNR77", source="web"
        )
        args, kwargs = korail_cancel.call_args
        assert args == (KORAIL_ID, "pw", "PNR77")
        assert kwargs["device"] == services.users.korail_device(KORAIL_ID)
        events.assert_not_awaited()  # 코레일봇 예약 기록이 없음

    @pytest.mark.asyncio
    async def test_marks_bot_reservation_of_same_account(
        self, services, fake_launcher, valid_request, korail_cancel, events
    ):
        mine = await _bot_reservation(services, fake_launcher, valid_request)
        # 다른 계정에 같은 예약번호가 있어도 건드리지 않음
        other = await _bot_reservation(
            services, fake_launcher, valid_request, korail_id="010-8765-4321"
        )
        events.reset_mock()

        await services.reservations.cancel_korail_rsv(
            "01012345678", "pw", "PNR1", source="telegram"
        )

        assert services.reservations.get(mine, owner=USER).korail_cancelled_at
        admin = Owner(user_id="admin", is_admin=True)
        assert services.reservations.get(other, owner=admin).korail_cancelled_at is None
        event = events.await_args.args[0]
        assert (event.kind, event.source, event.reservation.id) == (
            "korail_cancelled",
            "telegram",
            mine,
        )

    @pytest.mark.asyncio
    async def test_not_in_korail_list(self, services, korail_cancel):
        korail_cancel.return_value = False
        changed = Mock()
        services.reservations.korail_changed = changed
        with pytest.raises(Conflict, match="이미 결제"):
            await services.reservations.cancel_korail_rsv(
                KORAIL_ID, "pw", "PNR1", source="web"
            )
        changed.assert_called_once_with(KORAIL_ID)

    @pytest.mark.asyncio
    async def test_cancel_clears_account_cache(self, services, korail_cancel):
        services.korail_account._cache["01012345678"] = (0.0, Mock())
        services.korail_account._clock = lambda: 1.0
        assert services.korail_account.cached(KORAIL_ID) is not None
        await services.reservations.cancel_korail_rsv(
            KORAIL_ID, "pw", "PNR1", source="web"
        )
        assert services.korail_account.cached(KORAIL_ID) is None

    @pytest.mark.asyncio
    async def test_korail_links(self, services, fake_launcher, valid_request):
        rid = await _bot_reservation(services, fake_launcher, valid_request, rsv="PNR5")
        await _bot_reservation(
            services,
            fake_launcher,
            valid_request,
            korail_id="010-8765-4321",
            rsv="PNR6",
        )
        assert services.reservations.korail_links(
            "01012345678", {"PNR5", "PNR6", "PNR7"}
        ) == {"PNR5": rid}
        assert services.reservations.korail_links("01012345678", {"PNR7"}) == {}


def _account_out(*rsv_ids):
    from core.korail_account import KorailAccountOut, KorailReservationOut

    return KorailAccountOut(
        korail_id=KORAIL_ID,
        reservations=[
            KorailReservationOut(
                rsv_id=rsv,
                train_no="00403",
                train_type="KTX",
                dep_date="20991031",
                dep_time="0917",
                arr_time="1024",
                dep_station="서울",
                arr_station="서대전",
                price=23700,
                seat_count=1,
                waiting=rsv.endswith("W"),
                pay_deadline=(
                    None if rsv.endswith("W") else datetime(2099, 10, 31, 14, 24)
                ),
            )
            for rsv in rsv_ids
        ],
        tickets=[],
        fetched_at=datetime(2099, 10, 31, 14, 10),
    )


class TestWebApi:
    @pytest.fixture
    async def client(self, services, tmp_path):
        import httpx

        from web.factory import create_app

        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        app = create_app(
            services.settings, services, run_housekeeping=False, dist_dir=str(dist)
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            login = await c.post(
                "/api/auth/login", json={"phone": KORAIL_ID, "password": "correct"}
            )
            c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            yield c

    @pytest.mark.asyncio
    async def test_account_uses_session_credentials(self, client, services):
        calls = []
        services.korail_account.overview = lambda kid, pw, refresh=False: (
            calls.append((kid, pw, refresh)) or _account_out("PNR1")
        )
        response = await client.get("/api/korail/account")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reservations"][0]["rsv_id"] == "PNR1"
        assert body["reservations"][0]["pay_deadline"].startswith("2099-10-31T14:24")
        await client.get("/api/korail/account", params={"refresh": "true"})
        assert calls == [(KORAIL_ID, "correct", False), (KORAIL_ID, "correct", True)]

    @pytest.mark.asyncio
    async def test_cancel_returns_list_without_it(
        self, client, services, korail_cancel
    ):
        services.korail_account._cache["01012345678"] = (
            0.0,
            _account_out("PNR1", "PNR2"),
        )
        services.korail_account._clock = lambda: 1.0
        services.korail_account.overview = Mock(
            side_effect=AssertionError("no refetch")
        )

        response = await client.post("/api/korail/reservations/PNR1/cancel")

        assert response.status_code == 200, response.text
        assert [r["rsv_id"] for r in response.json()["reservations"]] == ["PNR2"]
        assert korail_cancel.call_args.args[:3] == (KORAIL_ID, "correct", "PNR1")

    @pytest.mark.asyncio
    async def test_cancel_without_cache_does_not_refetch(
        self, client, services, korail_cancel
    ):
        # 취소는 끝났으므로 다시 조회하다 실패해 취소가 실패한 것처럼 보이지 않도록
        services.korail_account.overview = Mock(
            side_effect=AssertionError("no refetch")
        )
        response = await client.post("/api/korail/reservations/PNR1/cancel")
        assert response.status_code == 200, response.text
        assert response.json() is None
        korail_cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_rejects_odd_ids_and_needs_csrf(self, client, korail_cancel):
        response = await client.post("/api/korail/reservations/a%2Fb/cancel")
        assert response.status_code in (404, 422)
        del client.headers["X-CSRF-Token"]
        response = await client.post("/api/korail/reservations/PNR1/cancel")
        assert response.status_code == 403
        korail_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_login(self, services, tmp_path):
        import httpx

        from web.factory import create_app

        dist = tmp_path / "dist2"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        app = create_app(
            services.settings, services, run_housekeeping=False, dist_dir=str(dist)
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            assert (await c.get("/api/korail/account")).status_code == 401


class TestTelegram:
    @pytest.fixture
    def bot(self, services):
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
        bot.send_message = AsyncMock()
        return bot

    def _login(self, bot, chat_id=1):
        bot.ensure_user_exists(chat_id)
        bot.userDict[chat_id]["userInfo"].update(
            {
                "korailId": KORAIL_ID,
                "korailPw": "correct",
                "ownerId": USER.user_id,
                "korailLogin": (KORAIL_ID, "correct"),
            }
        )

    @pytest.mark.asyncio
    async def test_failed_login_is_not_a_login(self, bot, services):
        # 남의 전화번호 + 틀린 비밀번호로는 그 계정의 캐시도, 코레일 로그인도 쓰지 않음
        services.korail_account.cached = Mock(return_value=_account_out("PNR1"))
        services.korail_account.overview = Mock()
        bot._korail_login = Mock(return_value=(False, "비밀번호 오류"))
        bot.ensure_user_exists(1)
        bot.userDict[1]["userInfo"]["korailId"] = KORAIL_ID
        await bot._input_pw(1, "wrong")

        await bot._show_my_tickets(1)
        await bot._handle_my_tickets_callback(1, "kres_r")

        services.korail_account.cached.assert_not_called()
        services.korail_account.overview.assert_not_called()
        assert "/start" in bot.send_message.call_args.args[1]

    @pytest.mark.asyncio
    async def test_successful_login_then_new_phone(self, bot, services):
        bot._korail_login = Mock(return_value=(True, ""))
        bot.users.link_telegram = Mock()
        bot.ensure_user_exists(1)
        bot.userDict[1]["userInfo"].update({"korailId": KORAIL_ID, "ownerId": "x"})
        await bot._input_pw(1, "correct")
        assert bot._chat_korail_login(1) == (KORAIL_ID, "correct")
        # 다른 번호로 다시 로그인을 시작하면 확인 전까지는 로그인 없음
        bot.users.is_allowed = Mock(return_value=True)
        await bot._input_id(1, "010-8765-4321")
        assert bot._chat_korail_login(1) is None

    @pytest.mark.asyncio
    async def test_needs_login(self, bot, services):
        services.korail_account.overview = Mock()
        bot.ensure_user_exists(1)
        await bot._show_my_tickets(1)
        assert "/start" in bot.send_message.call_args.args[1]
        services.korail_account.overview.assert_not_called()

    @pytest.mark.asyncio
    async def test_lists_with_buttons(self, bot, services):
        self._login(bot)
        services.korail_account.overview = Mock(
            return_value=_account_out("PNR1", "PNR2W")
        )

        await bot._show_my_tickets(1)

        services.korail_account.overview.assert_called_once_with(
            KORAIL_ID, "correct", False
        )
        texts = [c.args[1] for c in bot.send_message.call_args_list]
        assert "불러오는 중" in texts[0]
        listing = texts[1]
        assert "결제 대기 2건" in listing
        assert "KTX 403" in listing and "서울→서대전" in listing
        assert "23:24까지 결제" in listing  # KST
        assert "예약대기" in listing
        keyboard = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
        assert [b.callback_data for b in keyboard[0]] == ["kres_c_PNR1", "kres_c_PNR2W"]
        assert keyboard[1][0].url.startswith("https://www.korail.com")
        assert keyboard[-1][0].callback_data == "kres_r"

    @pytest.mark.asyncio
    async def test_long_list_and_transfer_legs(self, bot, services):
        self._login(bot)
        data = _account_out(*[f"PNR{i}" for i in range(30)])
        # 환승 예약: 구간마다 항목이 있지만 예약번호는 하나
        data.reservations[1].rsv_id = "PNR0"
        services.korail_account.overview = Mock(return_value=data)

        await bot._show_my_tickets(1)

        listing = bot.send_message.call_args.args[1]
        assert len(listing) < 4096
        assert "결제 대기 30건" in listing and "외 20건" in listing
        keyboard = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
        ids = [b.callback_data for row in keyboard for b in row if b.callback_data]
        cancel_ids = [i for i in ids if i.startswith("kres_c_")]
        assert len(cancel_ids) == len(set(cancel_ids)) == 9

    def test_train_line_tolerates_bad_date(self, bot):
        item = _account_out("PNR1").reservations[0].model_copy(update={"dep_date": ""})
        assert "09:17" in bot._train_line(item)

    @pytest.mark.asyncio
    async def test_uses_cache_without_loading_message(self, bot, services):
        self._login(bot)
        services.korail_account.cached = Mock(return_value=_account_out("PNR1"))
        services.korail_account.overview = Mock()
        await bot._show_my_tickets(1)
        services.korail_account.overview.assert_not_called()
        assert bot.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_refresh_button_refetches(self, bot, services):
        self._login(bot)
        services.korail_account.overview = Mock(return_value=_account_out())
        await bot._handle_my_tickets_callback(1, "kres_r")
        services.korail_account.overview.assert_called_once_with(
            KORAIL_ID, "correct", True
        )
        assert "결제를 기다리는 예약이 없습니다" in bot.send_message.call_args.args[1]

    @pytest.mark.asyncio
    async def test_error_message(self, bot, services):
        self._login(bot)
        services.korail_account.overview = Mock(
            side_effect=KorailUnavailable("코레일 서버가 막았습니다")
        )
        await bot._show_my_tickets(1)
        assert bot.send_message.call_args.args[1] == "코레일 서버가 막았습니다"

    @pytest.mark.asyncio
    async def test_cancel_asks_then_cancels(self, bot, services, korail_cancel):
        self._login(bot)
        services.korail_account.cached = Mock(return_value=_account_out("PNR1"))

        await bot._handle_my_tickets_callback(1, "kres_c_PNR1")
        msg = bot.send_message.call_args.args[1]
        assert "취소할까요" in msg and "서울→서대전" in msg
        keyboard = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
        assert keyboard[0][0].callback_data == "kres_y_PNR1"
        korail_cancel.assert_not_called()

        await bot._handle_my_tickets_callback(1, "kres_y_PNR1")
        assert korail_cancel.call_args.args[:3] == (KORAIL_ID, "correct", "PNR1")
        assert "취소했습니다" in bot.send_message.call_args.args[1]

    @pytest.mark.asyncio
    async def test_cancel_failure_is_reported(self, bot, services, korail_cancel):
        self._login(bot)
        korail_cancel.return_value = False
        await bot._handle_my_tickets_callback(1, "kres_y_PNR1")
        assert "이미 결제" in bot.send_message.call_args.args[1]

    def test_command_is_registered_and_in_help(self, bot):
        from telegramBot.messages import Messages

        assert "/mytickets" in Messages.Info.HELP_MESSAGE
        commands = {
            c.args[0].commands
            for c in bot.app.add_handler.call_args_list
            if hasattr(c.args[0], "commands")
        }
        assert frozenset({"mytickets"}) in commands


class TestLoginFailureKinds:
    @pytest.mark.parametrize(
        "error, rejected",
        [
            ("wrong", True),
            ("fallback", False),
            ("password_change", False),
            ("outdated", False),
            ("http", False),
        ],
    )
    def test_only_credential_rejections_count(self, error, rejected):
        from pykorail import (
            HttpStatusError,
            LoginFailedError,
            PasswordChangeRequiredError,
        )

        from telegramBot.korail_client import (
            PYKORAIL_FALLBACK_LOGIN_MSG,
            ReserveHandler,
        )

        e = {
            "wrong": LoginFailedError("비밀번호가 틀렸습니다", "WRR000101"),
            "fallback": LoginFailedError(PYKORAIL_FALLBACK_LOGIN_MSG, None),
            "password_change": PasswordChangeRequiredError("변경", "WRC000420"),
            "outdated": LoginFailedError("최신 버전으로 업데이트", "-8204"),
            "http": HttpStatusError(502, "bad gateway"),
        }[error]
        assert ReserveHandler._credentials_rejected(e) is rejected

    def test_cancel_maps_app_outdated_like_account(self):
        from core.auth import default_korail_cancel
        from telegramBot.korail_client import APP_OUTDATED_MESSAGE

        handler = FakeHandler(login_ok=False, error=APP_OUTDATED_MESSAGE)
        with pytest.raises(KorailUnavailable) as exc:
            default_korail_cancel(
                KORAIL_ID, "pw", "PNR1", handler_cls=lambda **_: handler
            )
        assert exc.value.code == "KORAIL_APP_OUTDATED"
        assert handler.closed
