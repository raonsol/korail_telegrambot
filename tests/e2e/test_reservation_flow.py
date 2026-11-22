"""
End-to-end tests for complete reservation flow (Telegram channel)
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.schemas import Owner, WorkerEvent


@pytest.mark.e2e
@pytest.mark.slow
class TestCompleteReservationFlow:
    """Test complete reservation flow from start to finish"""

    @pytest.fixture
    def bot_with_mocks(self, services):
        """Create bot instance with all necessary mocks"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
            services.notifier.add(bot)
            bot.send_message = AsyncMock()
            return bot

    @pytest.mark.asyncio
    async def test_full_reservation_flow(self, bot_with_mocks, fake_launcher):
        """Test complete reservation flow from /start to reservation result"""
        bot = bot_with_mocks
        chat_id = 123456

        # Step 1: Start command
        update = Mock()
        update.message = Mock()
        update.message.chat_id = chat_id

        await bot.start_func(update, None)
        assert chat_id in bot.userDict
        assert bot.userDict[chat_id]["inProgress"] is True
        assert bot.userDict[chat_id]["lastAction"] == 1

        # Step 2: Click "Start" button
        await bot._start_accept(chat_id, "start_yes")
        assert bot.userDict[chat_id]["lastAction"] == 2

        # Step 3: Input phone number (registered in user DB)
        await bot._input_id(chat_id, "01012345678")
        assert bot.userDict[chat_id]["lastAction"] == 3
        assert bot.userDict[chat_id]["userInfo"]["korailId"] == "010-1234-5678"

        # Step 4: Input password
        with patch("telegramBot.bot.ReserveHandler") as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=True)
            mock_handler_class.return_value = mock_handler

            await bot._input_pw(chat_id, "test_password")
            assert bot.userDict[chat_id]["lastAction"] == 4
            assert bot.userDict[chat_id]["userInfo"]["korailPw"] == "test_password"

        # Step 5: Select date (future date to pass validation)
        selected_date = datetime.now() + timedelta(days=1)
        await bot._input_date(chat_id, selected_date)
        assert bot.userDict[chat_id]["lastAction"] == 5
        expected_date = selected_date.strftime("%Y%m%d")
        assert bot.userDict[chat_id]["trainInfo"]["depDate"] == expected_date

        # Step 6: Search and select source station
        with patch(
            "telegramBot.bot.search_stations",
            return_value={
                "stations": [{"code": "0001", "name": "서울"}],
                "total": 1,
                "page": 1,
            },
        ):
            await bot._input_src_station(chat_id, "서울")
        # Station search keeps lastAction at 5 until user selects
        assert bot.userDict[chat_id]["lastAction"] == 5
        await bot._select_src_station(chat_id, "서울")
        assert bot.userDict[chat_id]["lastAction"] == 6
        assert bot.userDict[chat_id]["trainInfo"]["srcLocate"] == "서울"

        # Step 7: Search and select destination station
        with patch(
            "telegramBot.bot.search_stations",
            return_value={
                "stations": [{"code": "0020", "name": "부산"}],
                "total": 1,
                "page": 1,
            },
        ):
            await bot._input_dst_station(chat_id, "부산")
        assert bot.userDict[chat_id]["lastAction"] == 6
        await bot._select_dst_station(chat_id, "부산")
        assert bot.userDict[chat_id]["lastAction"] == 7
        assert bot.userDict[chat_id]["trainInfo"]["dstLocate"] == "부산"

        # Step 8: Input departure time
        await bot._input_dep_time(chat_id, "0900")
        assert bot.userDict[chat_id]["lastAction"] == 8
        assert bot.userDict[chat_id]["trainInfo"]["depTime"] == "0900"

        # Step 9: Input max departure time
        await bot._input_max_dep_time(chat_id, "1200")
        assert bot.userDict[chat_id]["lastAction"] == 9
        assert bot.userDict[chat_id]["trainInfo"]["maxDepTime"] == "1200"

        # Step 10: Select train type
        await bot._input_train_type(chat_id, "train_type_1")
        assert bot.userDict[chat_id]["lastAction"] == 10
        assert bot.userDict[chat_id]["trainInfo"]["trainType"] == "KTX"

        # Step 11: Select seat type → asks whether to use the waitlist (state 13)
        await bot._input_seat_type(chat_id, "seat_type_3")
        assert bot.userDict[chat_id]["lastAction"] == 13
        assert bot.userDict[chat_id]["trainInfo"]["specialInfo"] == "special"

        # Step 11-1: Turn on the waitlist → confirm (state 11)
        await bot._input_waitlist(chat_id, "waitlist_on")
        assert bot.userDict[chat_id]["lastAction"] == 11
        assert bot.userDict[chat_id]["trainInfo"]["allowWaitlist"] is True

        # Step 12: Confirm and start reservation
        await bot._start_reserve(chat_id, "confirm_yes")
        assert bot.userDict[chat_id]["lastAction"] == 12
        spec = fake_launcher.launched[0]
        assert spec["korail_id"] == "010-1234-5678"
        assert spec["seat_type"] == "special"
        assert spec["allow_waitlist"] is True
        [reservation] = bot._active_reservations(chat_id)

        # Step 13: Worker reports success → user is notified and state resets
        await bot.reservations.handle_worker_event(
            WorkerEvent(
                reservation_id=reservation.id,
                token=spec["callback_token"],
                status="success",
                train_info="KTX 101 서울~부산",
            )
        )
        last_message = bot.send_message.call_args[0][1]
        assert "예약에 성공" in last_message
        assert "KTX 101" in last_message
        assert bot.userDict[chat_id]["lastAction"] == 0
        assert bot._active_reservations(chat_id) == []

    @pytest.mark.asyncio
    async def test_other_users_not_blocked_by_running_reservation(
        self, bot_with_mocks, valid_request
    ):
        """다른 사용자의 예약이 진행 중이어도 대화를 계속할 수 있음"""
        bot = bot_with_mocks
        user1_id = 111111
        user2_id = 222222

        await bot.reservations.start(
            Owner(user_id="01012345678"),
            valid_request,
            "010-1234-5678",
            "pw",
            origin="telegram",
            chat_id=user1_id,
        )

        bot._create_user(user2_id)
        bot.userDict[user2_id]["inProgress"] = True
        bot.userDict[user2_id]["lastAction"] = 5
        bot.userDict[user2_id]["trainInfo"]["depDate"] = valid_request.dep_date_compact

        with patch(
            "telegramBot.bot.search_stations",
            return_value={
                "stations": [{"code": "0001", "name": "서울"}],
                "total": 1,
                "page": 1,
            },
        ):
            await bot.handle_progress(user2_id, 5, "서울")

        assert "검색 결과" in bot.send_message.call_args[0][1]


@pytest.mark.e2e
@pytest.mark.slow
class TestErrorRecovery:
    """Test error recovery scenarios"""

    @pytest.mark.asyncio
    async def test_worker_exit_without_report_is_error(self, services, valid_request):
        """Worker process that exits without reporting is marked as error"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
            services.notifier.add(bot)
            bot.send_message = AsyncMock()
            chat_id = 123456

            reservation = await services.reservations.start(
                Owner(user_id="01012345678"),
                valid_request,
                "010-1234-5678",
                "pw",
                origin="telegram",
                chat_id=chat_id,
            )

            await services.reservations.handle_process_exit(reservation.id, -9)

            assert bot._active_reservations(chat_id) == []
            r = services.reservations.get(reservation.id, chat_id=chat_id)
            assert r.status.value == "error"
            assert "비정상 종료" in bot.send_message.call_args[0][1]
