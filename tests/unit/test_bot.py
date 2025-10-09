"""
Unit tests for Telegram bot (bot.py)
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio


class TestTelegramBot:
    """Test TelegramBot class"""

    @pytest.fixture
    def bot_instance(self):
        """Create a TelegramBot instance for testing"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            return bot

    def test_bot_initialization_subprocess_mode(self):
        """Test bot initialization in subprocess mode"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)

            assert bot.token == "test_token"
            assert bot.use_celery is False
            assert bot.redis_client is None
            assert bot.celery_app is None
            assert isinstance(bot.userDict, dict)
            assert isinstance(bot.runningStatus, dict)
            assert isinstance(bot.subscribes, list)

    def test_bot_initialization_celery_mode_no_redis(self):
        """Test bot initialization in Celery mode when Redis is not available"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", False
        ):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=True)

            assert bot.use_celery is False
            assert bot.redis_client is None
            assert bot.celery_app is None

    def test_create_user(self, bot_instance):
        """Test _create_user method"""
        chat_id = 123456

        bot_instance._create_user(chat_id)

        assert chat_id in bot_instance.userDict
        assert bot_instance.userDict[chat_id]["inProgress"] is False
        assert bot_instance.userDict[chat_id]["lastAction"] == 0
        assert "userInfo" in bot_instance.userDict[chat_id]
        assert "trainInfo" in bot_instance.userDict[chat_id]

    def test_ensure_user_exists(self, bot_instance):
        """Test ensure_user_exists method"""
        chat_id = 123456

        # User doesn't exist yet
        bot_instance.ensure_user_exists(chat_id)
        assert chat_id in bot_instance.userDict

        # User already exists
        original_data = bot_instance.userDict[chat_id].copy()
        bot_instance.ensure_user_exists(chat_id)
        assert bot_instance.userDict[chat_id] == original_data

    def test_reset_user_state(self, bot_instance, sample_user_data):
        """Test _reset_user_state method"""
        chat_id = 123456
        bot_instance.userDict[chat_id] = sample_user_data.copy()

        bot_instance._reset_user_state(chat_id)

        assert bot_instance.userDict[chat_id]["inProgress"] is False
        assert bot_instance.userDict[chat_id]["lastAction"] == 0
        assert bot_instance.userDict[chat_id]["trainInfo"] == {}
        assert bot_instance.userDict[chat_id]["pid"] == 9999999

    def test_get_user_progress_existing_user(self, bot_instance, sample_user_data):
        """Test _get_user_progress for existing user"""
        chat_id = 123456
        bot_instance.userDict[chat_id] = sample_user_data.copy()

        in_progress, progress_num = bot_instance._get_user_progress(chat_id)

        assert in_progress is True
        assert progress_num == 4

    def test_get_user_progress_new_user(self, bot_instance):
        """Test _get_user_progress for new user"""
        chat_id = 999999

        in_progress, progress_num = bot_instance._get_user_progress(chat_id)

        assert in_progress is False
        assert progress_num == 0
        assert chat_id in bot_instance.userDict

    @pytest.mark.asyncio
    async def test_send_message_success(self, bot_instance):
        """Test send_message method successfully sends message"""
        chat_id = 123456
        test_message = "Test message"

        bot_instance.app.bot.send_message = AsyncMock(return_value=Mock())

        result = await bot_instance.send_message(chat_id, test_message)

        assert result is not None
        assert bot_instance.lastSentMessage == test_message
        bot_instance.app.bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_failure(self, bot_instance):
        """Test send_message handles errors gracefully"""
        from telegram.error import TelegramError

        chat_id = 123456
        test_message = "Test message"

        bot_instance.app.bot.send_message = AsyncMock(
            side_effect=TelegramError("Network error")
        )

        result = await bot_instance.send_message(chat_id, test_message)

        assert result is None

    @pytest.mark.asyncio
    async def test_start_func(self, bot_instance, mock_telegram_update):
        """Test /start command handler"""
        mock_telegram_update.message.chat_id = 123456
        bot_instance.send_message = AsyncMock()

        await bot_instance.start_func(mock_telegram_update, None)

        assert 123456 in bot_instance.userDict
        assert bot_instance.userDict[123456]["inProgress"] is True
        assert bot_instance.userDict[123456]["lastAction"] == 1
        bot_instance.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_accept_admin_login(self, bot_instance):
        """Test _start_accept with admin password"""
        chat_id = 123456
        bot_instance.userDict[chat_id] = {
            "inProgress": True,
            "lastAction": 1,
            "userInfo": {"korailId": "no-login-yet", "korailPw": "no-login-yet"},
            "trainInfo": {},
            "pid": 9999999,
        }

        with patch("telegramBot.bot.settings") as mock_settings, patch(
            "telegramBot.bot.ReserveHandler"
        ) as mock_handler_class:
            mock_settings.admin_password = "admin123"
            mock_settings.admin_korail_id = "admin_id"
            mock_settings.admin_korail_pw = "admin_pw"

            mock_handler = Mock()
            mock_handler.login = Mock(return_value=True)
            mock_handler_class.return_value = mock_handler

            bot_instance.send_message = AsyncMock()

            await bot_instance._start_accept(chat_id, "admin123")

            assert bot_instance.userDict[chat_id]["userInfo"]["korailId"] == "admin_id"
            assert bot_instance.userDict[chat_id]["userInfo"]["korailPw"] == "admin_pw"
            assert bot_instance.userDict[chat_id]["lastAction"] == 4

    @pytest.mark.asyncio
    async def test_input_id_valid_phone(self, bot_instance):
        """Test _input_id with valid phone number"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["inProgress"] = True
        bot_instance.userDict[chat_id]["lastAction"] = 2

        with patch("telegramBot.bot.settings") as mock_settings:
            mock_settings.allow_list = "01012345678,01087654321"
            bot_instance.send_message = AsyncMock()

            await bot_instance._input_id(chat_id, "01012345678")

            assert (
                bot_instance.userDict[chat_id]["userInfo"]["korailId"]
                == "010-1234-5678"
            )
            assert bot_instance.userDict[chat_id]["lastAction"] == 3

    @pytest.mark.asyncio
    async def test_input_id_invalid_phone(self, bot_instance):
        """Test _input_id with invalid phone number format"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()

        await bot_instance._input_id(chat_id, "invalid")

        assert bot_instance.userDict[chat_id]["lastAction"] == 0
        bot_instance.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_input_id_unauthorized_phone(self, bot_instance):
        """Test _input_id with unauthorized phone number"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        bot_instance.broadcast_message = AsyncMock()

        with patch("telegramBot.bot.settings") as mock_settings:
            mock_settings.allow_list = "01012345678"

            await bot_instance._input_id(chat_id, "01099999999")

            assert bot_instance.userDict[chat_id]["inProgress"] is False
            bot_instance.broadcast_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_input_pw_success(self, bot_instance):
        """Test _input_pw with successful login"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"

        with patch("telegramBot.bot.ReserveHandler") as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=True)
            mock_handler_class.return_value = mock_handler

            bot_instance.send_message = AsyncMock()

            await bot_instance._input_pw(chat_id, "test_password")

            assert (
                bot_instance.userDict[chat_id]["userInfo"]["korailPw"]
                == "test_password"
            )
            assert bot_instance.userDict[chat_id]["lastAction"] == 4

    @pytest.mark.asyncio
    async def test_input_pw_failure(self, bot_instance):
        """Test _input_pw with failed login"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"
        # Set lastAction to 3 (password input stage) before testing
        bot_instance.userDict[chat_id]["lastAction"] = 3

        with patch("telegramBot.bot.ReserveHandler") as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=False)
            mock_handler_class.return_value = mock_handler

            bot_instance.send_message = AsyncMock()

            await bot_instance._input_pw(chat_id, "wrong_password")

            # Should stay at password input stage (lastAction unchanged on failure)
            assert bot_instance.userDict[chat_id]["lastAction"] == 3

    @pytest.mark.asyncio
    async def test_subscribe_user(self, bot_instance, mock_telegram_update):
        """Test /subscribe command handler"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance.send_message = AsyncMock()

        await bot_instance.subscribe_user(mock_telegram_update, None)

        assert chat_id in bot_instance.subscribes
        bot_instance.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_message(self, bot_instance):
        """Test broadcast_message method"""
        bot_instance.subscribes = [123456, 789012]
        bot_instance.send_message = AsyncMock()

        await bot_instance.broadcast_message("Test broadcast")

        assert bot_instance.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_cancel_func_no_reservation(self, bot_instance, mock_telegram_update):
        """Test /cancel command when no reservation is running"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance.send_message = AsyncMock()

        await bot_instance.cancel_func(mock_telegram_update, None)

        bot_instance.send_message.assert_called_once()
        args = bot_instance.send_message.call_args[0]
        assert "진행중인 예약이 없습니다" in args[1]

    @pytest.mark.asyncio
    async def test_cancel_func_with_reservation(
        self, bot_instance, mock_telegram_update
    ):
        """Test /cancel command with active reservation"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"
        bot_instance.runningStatus[chat_id] = {"pid": 12345, "method": "subprocess"}
        bot_instance.send_message = AsyncMock()
        bot_instance.broadcast_message = AsyncMock()

        with patch("telegramBot.bot.os.killpg"):
            with patch("telegramBot.bot.os.getpgid"):
                await bot_instance.cancel_func(mock_telegram_update, None)

        assert chat_id not in bot_instance.runningStatus
        bot_instance.broadcast_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_background_process(self, bot_instance):
        """Test _start_background_process method"""
        arguments = [
            "010-1234-5678",
            "password",
            "20250115",
            "서울",
            "부산",
            "090000",
            "KTX",
            "1",
            "123456",
            "1200",
        ]

        with patch("telegramBot.bot.subprocess.Popen") as mock_popen, patch(
            "telegramBot.bot.os.makedirs"
        ), patch("builtins.open"), patch("telegramBot.bot.threading.Thread"):
            mock_process = Mock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            pid = bot_instance._start_background_process(arguments)

            assert pid == 12345
            mock_popen.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_status_info(self, bot_instance, mock_telegram_update):
        """Test /status command handler"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance.runningStatus = {
            789012: {"korailId": "010-1111-1111", "method": "subprocess"}
        }
        bot_instance.send_message = AsyncMock()

        await bot_instance.get_status_info(mock_telegram_update, None)

        bot_instance.send_message.assert_called_once()
        args = bot_instance.send_message.call_args[0]
        assert "1개의 예약이 실행중입니다" in args[1]


class TestUtilityFunctions:
    """Test utility functions in bot.py"""

    def test_is_affirmative(self):
        """Test is_affirmative function"""
        from telegramBot.bot import is_affirmative

        assert is_affirmative("Y") is True
        assert is_affirmative("y") is True
        assert is_affirmative("예") is True
        assert is_affirmative("N") is False
        assert is_affirmative("no") is False

    def test_is_negative(self):
        """Test is_negative function"""
        from telegramBot.bot import is_negative

        assert is_negative("N") is True
        assert is_negative("n") is True
        assert is_negative("아니오") is True
        assert is_negative("Y") is False
        assert is_negative("yes") is False

    def test_is_valid_time(self):
        """Test is_valid_time function"""
        from telegramBot.bot import is_valid_time

        assert is_valid_time("0900") is True
        assert is_valid_time("2359") is True
        assert is_valid_time("0000") is True
        assert is_valid_time("09:00") is False
        assert is_valid_time("2400") is False
        assert is_valid_time("abc") is False
        assert is_valid_time("12") is False

    def test_is_today(self):
        """Test is_today function"""
        from telegramBot.bot import is_today
        from datetime import datetime

        today = datetime.today().strftime("%Y%m%d")

        assert is_today(today) is True
        assert is_today("20200101") is False
