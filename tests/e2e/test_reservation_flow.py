"""
End-to-end tests for complete reservation flow
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from telegram import Update
from korail2 import TrainType, ReserveOption


@pytest.mark.e2e
@pytest.mark.slow
class TestCompleteReservationFlow:
    """Test complete reservation flow from start to finish"""

    @pytest.fixture
    def bot_with_mocks(self):
        """Create bot instance with all necessary mocks"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()
            return bot

    @pytest.mark.asyncio
    async def test_full_reservation_flow_subprocess_mode(self, bot_with_mocks):
        """Test complete reservation flow from /start to reservation"""
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

        # Step 3: Input phone number (ID)
        with patch("telegramBot.bot.settings") as mock_settings:
            mock_settings.allow_list = "01012345678"
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

        # Step 5: Select date
        from datetime import datetime, timedelta

        # Use a future date (tomorrow) to ensure it passes validation
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
        # Simulate user clicking the station button
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
        assert bot.userDict[chat_id]["trainInfo"]["trainType"] == TrainType.KTX

        # Step 11: Select seat type
        await bot._input_seat_type(chat_id, "seat_type_1")
        assert bot.userDict[chat_id]["lastAction"] == 11
        assert (
            bot.userDict[chat_id]["trainInfo"]["specialInfo"]
            == ReserveOption.GENERAL_FIRST
        )

        # Step 12: Confirm and start reservation
        with patch.object(bot, "_start_background_process", return_value=12345):
            await bot._start_reserve(chat_id, "confirm_yes")

            assert bot.userDict[chat_id]["lastAction"] == 12
            assert "12345" in bot.runningStatus
            assert bot.runningStatus["12345"]["chat_id"] == chat_id

    @pytest.mark.asyncio
    async def test_full_reservation_flow_cancellation(self, bot_with_mocks):
        """Test user cancels during reservation flow"""
        bot = bot_with_mocks
        chat_id = 123456

        # Setup user in progress
        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True
        bot.userDict[chat_id]["lastAction"] = 5

        # User sends /cancel at any point
        update = Mock()
        update.message = Mock()
        update.message.chat_id = chat_id

        bot.runningStatus["12345"] = {
            "chat_id": chat_id,
            "pid": 12345,
            "korailId": "010-1234-5678",
            "method": "subprocess",
        }

        with patch("telegramBot.bot.os.killpg"), patch("telegramBot.bot.os.getpgid"):
            await bot.cancel_func(update, None)
            await bot._handle_cancel_callback(chat_id, "cancel_all")

        # Should reset state
        assert "12345" not in bot.runningStatus

    @pytest.mark.asyncio
    async def test_reservation_flow_with_admin_login(self, bot_with_mocks):
        """Test reservation flow using admin quick login"""
        bot = bot_with_mocks
        chat_id = 123456

        # Start
        update = Mock()
        update.message = Mock()
        update.message.chat_id = chat_id

        await bot.start_func(update, None)

        # Use admin password for quick login
        with patch("telegramBot.bot.settings") as mock_settings, patch(
            "telegramBot.bot.ReserveHandler"
        ) as mock_handler_class:
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

    @pytest.mark.asyncio
    async def test_reservation_flow_login_failure_recovery(self, bot_with_mocks):
        """Test handling of login failure and retry"""
        bot = bot_with_mocks
        chat_id = 123456

        bot._create_user(chat_id)
        bot.userDict[chat_id]["inProgress"] = True
        bot.userDict[chat_id]["lastAction"] = 3
        bot.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"

        # First attempt: wrong password
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
        from datetime import datetime, timedelta

        bot.userDict[chat_id]["lastAction"] = 7
        future_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        bot.userDict[chat_id]["trainInfo"]["depDate"] = future_date
        await bot._input_dep_time(chat_id, "invalid_time")
        # Should send error message but stay at same stage

        # Past time for today's date
        today = datetime.today().strftime("%Y%m%d")
        bot.userDict[chat_id]["trainInfo"]["depDate"] = today
        bot.userDict[chat_id]["lastAction"] = 7
        await bot._input_dep_time(chat_id, "0001")  # Past time
        # Should send error message

    @pytest.mark.asyncio
    async def test_concurrent_reservation_prevention(self, bot_with_mocks):
        """Test that concurrent reservations are prevented in subprocess mode"""
        bot = bot_with_mocks

        user1_id = 111111
        user2_id = 222222

        # User 1 starts reservation
        bot.runningStatus["12345"] = {
            "chat_id": user1_id,
            "pid": 12345,
            "korailId": "010-1111-1111",
            "method": "subprocess",
        }

        # User 2 tries to proceed
        bot._create_user(user2_id)
        bot.userDict[user2_id]["inProgress"] = True
        bot.userDict[user2_id]["lastAction"] = 4

        # Should be prevented
        await bot.handle_progress(user2_id, 5, "서울")

        # Should receive message about another user
        assert bot.send_message.called


@pytest.mark.e2e
@pytest.mark.slow
class TestErrorRecovery:
    """Test error recovery scenarios"""

    @pytest.mark.asyncio
    async def test_subprocess_crash_recovery(self):
        """Test recovery when subprocess crashes"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()
            chat_id = 123456

            # Simulate crashed process
            bot._create_user(chat_id)
            bot.runningStatus["99999"] = {
                "chat_id": chat_id,
                "pid": 99999,
                "korailId": "010-1234-5678",
                "method": "subprocess",
            }

            # Try to cancel
            with patch("telegramBot.bot.os.killpg", side_effect=ProcessLookupError()):
                with patch("telegramBot.bot.os.getpgid", return_value=99999):
                    success = await bot._cancel_reservation(chat_id)

            # Should clean up state even if process doesn't exist
            assert "99999" not in bot.runningStatus

    @pytest.mark.asyncio
    async def test_network_error_during_callback(self):
        """Test handling of network errors during callback"""
        with patch("telegramBot.korail_client.requests.session") as mock_session:
            mock_post = Mock(side_effect=Exception("Network error"))
            mock_session.return_value.post = mock_post

            from telegramBot.korail_client import ReserveHandler

            handler = ReserveHandler()
            handler.chatId = "123456"

            # Should not crash on network error
            handler.sendBotStateChange("123456", "Test message", 1)


@pytest.mark.e2e
@pytest.mark.slow
class TestUserExperience:
    """Test overall user experience flows"""

    @pytest.mark.asyncio
    async def test_help_command(self):
        """Test /help command"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()

            update = Mock()
            update.message = Mock()
            update.message.chat_id = 123456

            await bot.return_help(update, None)

            bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_and_broadcast(self):
        """Test subscribe and broadcast functionality"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()

            # User subscribes
            update = Mock()
            update.message = Mock()
            update.message.chat_id = 123456

            await bot.subscribe_user(update, None)
            assert 123456 in bot.subscribes

            # Broadcast message
            await bot.broadcast_message("Test broadcast")
            assert bot.send_message.call_count >= 1

    @pytest.mark.asyncio
    async def test_status_command(self):
        """Test /status command"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()

            # Add some running reservations
            bot.runningStatus[111111] = {
                "korailId": "010-1111-1111",
                "method": "subprocess",
            }
            bot.runningStatus[222222] = {
                "korailId": "010-2222-2222",
                "method": "subprocess",
            }

            update = Mock()
            update.message = Mock()
            update.message.chat_id = 123456

            await bot.get_status_info(update, None)

            bot.send_message.assert_called_once()
            call_args = bot.send_message.call_args[0]
            assert "2개의 예약이 실행중입니다" in call_args[1]
