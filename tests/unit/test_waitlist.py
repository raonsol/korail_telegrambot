"""예약대기: 범위 안의 열차가 모두 매진이면 예약대기를 신청"""

from unittest.mock import Mock

import pytest
from pykorail import ReserveOption, SoldOutError, TrainType

from core.notifier import ReservationEvent, push_message
from core.runner import run_reservation
from core.schemas import Owner, ReservationOut, WorkerEvent


def _train(dep_time, seat=False, waitlist=False):
    train = Mock()
    train.dep_time = dep_time
    train.has_seat.return_value = seat
    train.has_waiting_list.return_value = waitlist
    return train


def _handler(trains, create):
    from telegramBot.korail_client import ReserveHandler

    handler = ReserveHandler()
    handler.korail_client = Mock()
    handler.korail_client.trains.search = Mock(return_value=trains)
    handler.korail_client.reservations.create = Mock(side_effect=create)
    return handler


def _attempt(handler, special=ReserveOption.GENERAL_FIRST, waitlist=True):
    return handler.reserve_single_attempt(
        depDate="20991231",
        srcLocate="서울",
        dstLocate="부산",
        depTime="090000",
        trainType=TrainType.KTX,
        special=special,
        maxDepTime="1200",
        allowWaitlist=waitlist,
    )


def _waiting_reservation():
    reservation = Mock()
    reservation.is_waiting = True
    return reservation


class TestReserveHandlerWaitlist:
    @pytest.mark.parametrize("waitlist", [True, False])
    def test_search_includes_waitlist_trains_only_when_enabled(self, waitlist):
        handler = _handler([], create=[])
        _attempt(handler, waitlist=waitlist)

        kwargs = handler.korail_client.trains.search.call_args.kwargs
        assert kwargs["include_waiting_list"] is waitlist

    def test_disabled_waitlist_keeps_searching(self):
        handler = _handler([_train("090000", waitlist=True)], create=[])

        result = _attempt(handler, waitlist=False)

        assert result["error"] == "All trains sold out"
        handler.korail_client.reservations.create.assert_not_called()

    def test_default_is_disabled(self):
        handler = _handler([_train("090000", waitlist=True)], create=[])

        result = handler.reserve_single_attempt(
            depDate="20991231", srcLocate="서울", dstLocate="부산", maxDepTime="1200"
        )

        assert result["success"] is False
        handler.korail_client.reservations.create.assert_not_called()

    def test_all_sold_out_registers_waitlist_on_earliest_open_train(self):
        closed = _train("090000")
        open_1 = _train("093000", waitlist=True)
        open_2 = _train("100000", waitlist=True)
        reservation = _waiting_reservation()
        handler = _handler([closed, open_1, open_2], create=[reservation])

        result = _attempt(handler)

        assert result == {
            "success": True,
            "result": reservation,
            "error": None,
            "waiting": True,
        }
        create = handler.korail_client.reservations.create
        create.assert_called_once_with(open_1, option=ReserveOption.GENERAL_ONLY)

    def test_seat_is_preferred_over_earlier_waitlist(self):
        waitlist_first = _train("090000", waitlist=True)
        seated_later = _train("110000", seat=True)
        seat = Mock(is_waiting=False)
        handler = _handler([waitlist_first, seated_later], create=[seat])

        result = _attempt(handler)

        assert result["result"] is seat
        assert result["waiting"] is False
        create = handler.korail_client.reservations.create
        create.assert_called_once_with(seated_later, option=ReserveOption.GENERAL_FIRST)

    def test_waitlist_after_seat_attempts_sell_out(self):
        seated = _train("090000", seat=True)
        waitable = _train("100000", waitlist=True)
        reservation = _waiting_reservation()
        handler = _handler([seated, waitable], create=[SoldOutError(), reservation])

        result = _attempt(handler)

        assert result["waiting"] is True
        assert handler.korail_client.reservations.create.call_count == 2

    def test_special_only_never_waitlists(self):
        handler = _handler([_train("090000", waitlist=True)], create=[])

        result = _attempt(handler, special=ReserveOption.SPECIAL_ONLY)

        assert result == {
            "success": False,
            "result": None,
            "error": "All trains sold out",
        }
        handler.korail_client.reservations.create.assert_not_called()

    def test_special_first_requests_general_waitlist(self):
        waitable = _train("090000", waitlist=True)
        handler = _handler([waitable], create=[_waiting_reservation()])

        _attempt(handler, special=ReserveOption.SPECIAL_FIRST)

        handler.korail_client.reservations.create.assert_called_once_with(
            waitable, option=ReserveOption.GENERAL_ONLY
        )

    def test_closed_waitlist_tries_next_train(self):
        first = _train("090000", waitlist=True)
        second = _train("100000", waitlist=True)
        reservation = _waiting_reservation()
        handler = _handler([first, second], create=[SoldOutError(), reservation])

        assert _attempt(handler)["result"] is reservation

    def test_existing_waitlist_counts_as_duplicate(self):
        error = Exception("WRR800029 동일한 예약 내역이 있으니 확인하시기 바랍니다")
        handler = _handler([_train("090000", waitlist=True)], create=[error])

        result = _attempt(handler)

        assert result["success"] is True
        assert result["result"] == "duplicate_reservation"


SPEC = {
    "allow_waitlist": True,
    "reservation_id": "r1",
    "korail_id": "010-1234-5678",
    "korail_pw": "pw",
    "dep_date": "20250115",
    "src_station": "서울",
    "dst_station": "부산",
    "dep_time": "0900",
    "max_dep_time": "1200",
    "train_type": "ALL",
    "seat_type": "general",
}


def test_runner_reports_waitlist_success():
    handler = Mock()
    handler.login = Mock(return_value=True)
    handler.reserve_single_attempt = Mock(
        return_value={
            "success": True,
            "result": "KTX 101, 예약대기",
            "error": None,
            "waiting": True,
        }
    )
    reporter = Mock()
    on_success = Mock()

    result = run_reservation(
        SPEC,
        reporter,
        on_success=on_success,
        handler_factory=lambda _device: handler,
        sleep=lambda _: None,
    )

    assert result["status"] == "success"
    assert result["waiting"] is True
    on_success.assert_called_once_with(
        train_info="KTX 101, 예약대기", attempts=1, waiting=True
    )
    assert reporter.send.call_args.kwargs["waiting"] is True
    assert handler.reserve_single_attempt.call_args.kwargs["allowWaitlist"] is True


def test_runner_passes_disabled_waitlist_by_default():
    handler = Mock()
    handler.login = Mock(return_value=True)
    handler.reserve_single_attempt = Mock(
        return_value={"success": True, "result": "KTX 101", "error": None}
    )
    spec = {k: v for k, v in SPEC.items() if k != "allow_waitlist"}

    run_reservation(
        spec, Mock(), handler_factory=lambda _device: handler, sleep=lambda _: None
    )

    assert handler.reserve_single_attempt.call_args.kwargs["allowWaitlist"] is False


class TestReservationRequestWaitlist:
    def _request(self, **kwargs):
        from datetime import timedelta

        from core.schemas import ReservationRequest, now_kst

        return ReservationRequest(
            dep_date=now_kst().date() + timedelta(days=7),
            src_station="서울",
            dst_station="부산",
            dep_time="0900",
            max_dep_time="1200",
            **kwargs,
        )

    def test_default_is_off(self):
        assert self._request().allow_waitlist is False

    @pytest.mark.parametrize("seat_type", ["general", "general_only", "special"])
    def test_can_be_enabled(self, seat_type):
        assert self._request(seat_type=seat_type, allow_waitlist=True).allow_waitlist

    def test_special_only_never_waitlists(self):
        request = self._request(seat_type="special_only", allow_waitlist=True)
        assert request.allow_waitlist is False


class TestReservationServiceWaitlist:
    @pytest.mark.asyncio
    async def test_option_is_stored_and_sent_to_worker(
        self, services, fake_launcher, valid_request
    ):
        request = valid_request.model_copy(update={"allow_waitlist": True})
        r = await services.reservations.start(
            Owner(user_id="01012345678"), request, "010", "pw", origin="web"
        )

        assert r.allow_waitlist is True
        assert fake_launcher.launched[-1]["allow_waitlist"] is True

    @pytest.mark.asyncio
    async def test_worker_waitlist_success_is_recorded(
        self, services, fake_launcher, valid_request
    ):
        r = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )
        token = fake_launcher.launched[-1]["callback_token"]

        await services.reservations.handle_worker_event(
            WorkerEvent(
                reservation_id=r.id,
                token=token,
                status="success",
                train_info="KTX 101, 예약대기",
                waiting=True,
            )
        )

        out = services.reservations.get(r.id, Owner(user_id="01012345678"))
        assert out.status == "success"
        assert out.waitlisted is True

    @pytest.mark.asyncio
    async def test_recovered_waitlist_success(
        self, services, fake_launcher, valid_request
    ):
        r = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )
        fake_launcher.recover_success = lambda _id: {
            "train_info": "KTX 101, 예약대기",
            "attempts": 4,
            "waiting": True,
        }

        await services.reservations.handle_process_exit(r.id, 0)

        out = services.reservations.get(r.id, Owner(user_id="01012345678"))
        assert out.status == "success"
        assert out.waitlisted is True


def _out(waitlisted):
    from datetime import datetime

    now = datetime(2025, 1, 1)
    return ReservationOut(
        id="r1",
        owner_id="01012345678",
        origin="telegram",
        status="success",
        dep_date="20250115",
        src_station="서울",
        dst_station="부산",
        dep_time="0900",
        max_dep_time="1200",
        train_type="KTX",
        seat_type="general",
        train_type_label="KTX",
        seat_type_label="일반실 우선 예약",
        attempts=3,
        result_text="KTX 101",
        waitlisted=waitlisted,
        created_at=now,
        updated_at=now,
        is_active=False,
    )


def test_push_message_for_waitlist():
    msg = push_message(ReservationEvent(_out(True), "running", source="worker"))
    assert "예약대기" in msg["title"]
    assert "20분" not in msg["title"]

    seat = push_message(ReservationEvent(_out(False), "running", source="worker"))
    assert "20분" in seat["title"]


def test_telegram_message_for_waitlist():
    from telegramBot.bot import TelegramBot

    event = ReservationEvent(_out(True), "running", source="worker")
    msg = TelegramBot._notification_message(event)

    assert "예약대기를 신청했습니다" in msg
    assert "KTX 101" in msg
    assert "20분내에" not in msg


class TestTelegramWaitlistStep:
    @pytest.fixture
    def bot(self, services):
        from unittest.mock import AsyncMock, patch

        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
        bot.send_message = AsyncMock()
        return bot

    def _prepare(self, bot, valid_request, chat_id=123456):
        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True
        bot.userDict[chat_id]["lastAction"] = 10
        bot.userDict[chat_id]["userInfo"].update(
            {"korailId": "010-1234-5678", "korailPw": "pw", "ownerId": "01012345678"}
        )
        bot.userDict[chat_id]["trainInfo"].update(
            {
                "depDate": valid_request.dep_date_compact,
                "srcLocate": "서울",
                "dstLocate": "부산",
                "depTime": "0900",
                "maxDepTime": "1200",
                "trainType": "KTX",
                "trainTypeShow": "KTX",
            }
        )
        return chat_id

    @pytest.mark.asyncio
    async def test_seat_type_asks_waitlist_with_explanation(self, bot, valid_request):
        chat_id = self._prepare(bot, valid_request)

        await bot._input_seat_type(chat_id, "seat_type_1")

        assert bot.userDict[chat_id]["lastAction"] == 13
        text = bot.send_message.call_args.kwargs["text"]
        assert "예약대기란?" in text
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert buttons == ["waitlist_on", "waitlist_off"]

    @pytest.mark.asyncio
    async def test_special_only_skips_waitlist_step(self, bot, valid_request):
        chat_id = self._prepare(bot, valid_request)

        await bot._input_seat_type(chat_id, "seat_type_4")

        assert bot.userDict[chat_id]["lastAction"] == 11
        assert bot.userDict[chat_id]["trainInfo"]["allowWaitlist"] is False
        assert "예약대기 : 사용 안 함" in bot.send_message.call_args.kwargs["text"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer, enabled",
        [("waitlist_on", True), ("waitlist_off", False), ("예", True), ("N", False)],
    )
    async def test_choice_is_used_for_reservation(
        self, bot, valid_request, fake_launcher, answer, enabled
    ):
        chat_id = self._prepare(bot, valid_request)
        await bot._input_seat_type(chat_id, "seat_type_1")

        await bot.handle_progress(chat_id, 13, answer)
        assert bot.userDict[chat_id]["lastAction"] == 11
        confirm = bot.send_message.call_args.kwargs["text"]
        assert ("예약대기 : 사용 (" in confirm) is enabled

        await bot._start_reserve(chat_id, "confirm_yes")
        assert fake_launcher.launched[-1]["allow_waitlist"] is enabled

    @pytest.mark.asyncio
    async def test_invalid_answer_asks_again(self, bot, valid_request):
        chat_id = self._prepare(bot, valid_request)
        await bot._input_seat_type(chat_id, "seat_type_1")

        await bot.handle_progress(chat_id, 13, "몰라")

        assert bot.userDict[chat_id]["lastAction"] == 13
        assert "예약대기란?" in bot.send_message.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_stale_button_is_ignored(self, bot, valid_request):
        chat_id = self._prepare(bot, valid_request)
        bot.userDict[chat_id]["lastAction"] = 12  # 예약 진행 중

        await bot._input_waitlist(chat_id, "waitlist_on")

        assert bot.userDict[chat_id]["lastAction"] == 12
        bot.send_message.assert_not_called()
