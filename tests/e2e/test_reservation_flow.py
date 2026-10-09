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
    async def test_full_reservation_flow_subprocess_mode(
        self, bot_with_mocks, fake_launcher
    ):
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
    async def test_full_reservation_flow_cancellation(
        self, bot_with_mocks, valid_request, fake_launcher
    ):
        """Test user cancels a running reservation"""
        bot = bot_with_mocks
        chat_id = 123456

        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True
        bot.userDict[chat_id]["lastAction"] = 12
        bot.userDict[chat_id]["userInfo"]["ownerId"] = "01012345678"
        await bot.reservations.start(
            Owner(user_id="01012345678"),
            valid_request,
            "010-1234-5678",
            "pw",
            origin="telegram",
            chat_id=chat_id,
        )

        update = Mock()
        update.message = Mock()
        update.message.chat_id = chat_id

        await bot.cancel_func(update, None)
        await bot._handle_cancel_callback(chat_id, "cancel_all")

        assert bot._active_reservations(chat_id) == []
        assert fake_launcher.cancelled == ["ref-1"]
        assert bot.userDict[chat_id]["lastAction"] == 0

    @pytest.mark.asyncio
    async def test_reservation_flow_with_admin_login(self, bot_with_mocks):
        """Test reservation flow using admin quick login"""
        bot = bot_with_mocks
        chat_id = 123456

        update = Mock()
        update.message = Mock()
        update.message.chat_id = chat_id

        await bot.start_func(update, None)

        with (
            patch("telegramBot.bot.settings") as mock_settings,
            patch("telegramBot.bot.ReserveHandler") as mock_handler_class,
        ):
            mock_settings.admin_password = "admin123"
            mock_settings.admin_korail_id = "admin_user"
            mock_settings.admin_korail_pw = "admin_pass"

            mock_handler = Mock()
            mock_handler.login = Mock(return_value=True)
            mock_handler_class.return_value = mock_handler

            await bot._start_accept(chat_id, "admin123")

            # Should skip directly to date selection
            assert bot.userDict[chat_id]["lastAction"] == 4
            assert bot.userDict[chat_id]["userInfo"]["korailId"] == "admin_user"
            assert bot._chat_owner(chat_id) == Owner(user_id="admin", is_admin=True)

    @pytest.mark.asyncio
    async def test_reservation_flow_login_failure_recovery(self, bot_with_mocks):
        """Test handling of login failure and retry"""
        bot = bot_with_mocks
        chat_id = 123456

        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True
        bot.userDict[chat_id]["lastAction"] = 3
        bot.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"

        with patch("telegramBot.bot.ReserveHandler") as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=False)
            mock_handler_class.return_value = mock_handler

            await bot._input_pw(chat_id, "wrong_password")

            # Should stay at password input stage
            assert bot.userDict[chat_id]["lastAction"] == 3

        # User goes back and re-enters phone
        await bot._handle_login_callback(chat_id, "login_back")
        assert bot.userDict[chat_id]["lastAction"] == 2

    @pytest.mark.asyncio
    async def test_reservation_flow_invalid_inputs(self, bot_with_mocks):
        """Test handling of various invalid inputs"""
        bot = bot_with_mocks
        chat_id = 123456

        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True

        # Invalid phone number
        bot.userDict[chat_id]["lastAction"] = 2
        await bot._input_id(chat_id, "invalid_phone")
        assert bot.userDict[chat_id]["lastAction"] == 2

        # Invalid time format
        bot.userDict[chat_id]["lastAction"] = 7
        future_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        bot.userDict[chat_id]["trainInfo"]["depDate"] = future_date
        await bot._input_dep_time(chat_id, "invalid_time")
        assert bot.userDict[chat_id]["lastAction"] == 7

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
    async def test_subprocess_crash_recovery(self, services, valid_request):
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

    def test_network_error_during_callback(self):
        """Callback reporter does not crash on network errors"""
        import requests

        from core.runner import CallbackReporter

        reporter = CallbackReporter("http://127.0.0.1:1/internal/events", "id", "tok")
        reporter.session.post = Mock(
            side_effect=requests.exceptions.ConnectionError("Network error")
        )

        with patch("core.runner.time.sleep"):
            assert reporter.send("error", message="Test message") is False
        assert reporter.session.post.call_count == 3


@pytest.mark.e2e
@pytest.mark.slow
class TestUserExperience:
    """Test overall user experience flows"""

    @pytest.fixture
    def bot(self, services):
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
            bot.send_message = AsyncMock()
            return bot

    @pytest.mark.asyncio
    async def test_help_command(self, bot):
        """Test /help command"""
        update = Mock()
        update.message = Mock()
        update.message.chat_id = 123456

        await bot.return_help(update, None)

        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_and_broadcast(self, bot):
        """Test subscribe and broadcast functionality"""
        update = Mock()
        update.message = Mock()
        update.message.chat_id = 123456

        await bot.subscribe_user(update, None)
        assert 123456 in bot.subscribes

        await bot.broadcast_message("Test broadcast")
        assert bot.send_message.call_count >= 1

    @pytest.mark.asyncio
    async def test_status_command(self, bot, services, valid_request):
        """Test /status command"""
        await services.reservations.start(
            Owner(user_id="01011111111"), valid_request, "010", "pw", origin="web"
        )
        await services.reservations.start(
            Owner(user_id="01022222222"), valid_request, "010", "pw", origin="web"
        )

        update = Mock()
        update.message = Mock()
        update.message.chat_id = 123456

        # 일반 사용자: 본인 예약 수만 (다른 사용자의 전화번호는 보이지 않음)
        await bot.get_status_info(update, None)
        text = bot.send_message.call_args[0][1]
        assert "내 예약은 0개" in text
        assert "010-1111-1111" not in text

        # 관리자: 전체 예약과 사용자
        bot.userDict[123456]["userInfo"]["isAdmin"] = True
        await bot.get_status_info(update, None)
        call_args = bot.send_message.call_args[0]
        assert "2개의 예약이 실행중입니다" in call_args[1]
