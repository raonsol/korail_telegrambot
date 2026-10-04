"""
Unit tests for Telegram bot (bot.py)
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio


class TestTelegramBot:
    """Test TelegramBot class"""

    @pytest.fixture
    def bot_instance(self, services):
        """Create a TelegramBot instance for testing"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)
            services.notifier.add(bot)
            return bot

    def test_bot_initialization(self, services):
        """Bot keeps only conversation state and delegates reservations to services"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", services)

            assert bot.token == "test_token"
            assert bot.reservations is services.reservations
            assert bot.users is services.users
            assert isinstance(bot.userDict, dict)
            assert isinstance(bot.subscribes, list)
            assert not hasattr(bot, "runningStatus")

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
            assert bot_instance.userDict[chat_id]["userInfo"]["isAdmin"] is True
            assert bot_instance.userDict[chat_id]["lastAction"] == 4

    @pytest.mark.asyncio
    async def test_start_accept_admin_login_failure_shows_reason(self, bot_instance):
        """Admin login failure message includes the Korail failure reason"""
        chat_id = 123456
        bot_instance._create_user(chat_id)

        with patch("telegramBot.bot.settings") as mock_settings, patch(
            "telegramBot.bot.ReserveHandler"
        ) as mock_handler_class:
            mock_settings.admin_password = "admin123"
            mock_settings.admin_korail_id = "admin_id"
            mock_settings.admin_korail_pw = "wrong_pw"

            mock_handler = Mock()
            mock_handler.login = Mock(return_value=False)
            mock_handler.loginError = "아이디 또는 비밀번호가 올바르지 않습니다"
            mock_handler_class.return_value = mock_handler

            bot_instance.send_message = AsyncMock()

            await bot_instance._start_accept(chat_id, "admin123")

            msg = bot_instance.send_message.call_args[0][1]
            assert "관리자 계정으로 로그인에 실패하였습니다" in msg
            assert "사유 : 아이디 또는 비밀번호가 올바르지 않습니다" in msg

    @pytest.mark.asyncio
    async def test_input_id_valid_phone(self, bot_instance):
        """Test _input_id with valid phone number"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["inProgress"] = True
        bot_instance.userDict[chat_id]["lastAction"] = 2

        # 01012345678 is seeded into the user DB from ALLOW_LIST
        bot_instance.send_message = AsyncMock()

        await bot_instance._input_id(chat_id, "010-1234-5678")

        user_info = bot_instance.userDict[chat_id]["userInfo"]
        assert user_info["korailId"] == "010-1234-5678"
        assert user_info["ownerId"] == "01012345678"
        assert bot_instance.userDict[chat_id]["lastAction"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phone", ["01012345678", "010 1234 5678"])
    async def test_input_id_accepts_phone_without_hyphens(self, bot_instance, phone):
        """Phone numbers are accepted with or without hyphens"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["inProgress"] = True
        bot_instance.userDict[chat_id]["lastAction"] = 2
        bot_instance.send_message = AsyncMock()

        await bot_instance._input_id(chat_id, phone)

        user_info = bot_instance.userDict[chat_id]["userInfo"]
        assert user_info["korailId"] == "010-1234-5678"
        assert user_info["ownerId"] == "01012345678"
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

        await bot_instance._input_id(chat_id, "01099999999")

        assert bot_instance.userDict[chat_id]["inProgress"] is False
        bot_instance.broadcast_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_input_id_deactivated_user(self, bot_instance, services):
        """Deactivated users in the DB cannot log in"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        bot_instance.broadcast_message = AsyncMock()
        services.users.update("01012345678", is_active=False)

        await bot_instance._input_id(chat_id, "01012345678")

        assert bot_instance.userDict[chat_id]["lastAction"] == 0
        bot_instance.broadcast_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_input_pw_success(self, bot_instance):
        """Test _input_pw with successful login"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["korailId"] = "010-1234-5678"
        bot_instance.userDict[chat_id]["userInfo"]["ownerId"] = "01012345678"

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
            # chat is linked to the user so web reservations notify here too
            assert bot_instance.users.get("01012345678").telegram_chat_id == chat_id

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
            mock_handler.loginError = "아이디 또는 비밀번호가 올바르지 않습니다"
            mock_handler_class.return_value = mock_handler

            bot_instance.send_message = AsyncMock()

            await bot_instance._input_pw(chat_id, "wrong_password")

            # Should stay at password input stage (lastAction unchanged on failure)
            assert bot_instance.userDict[chat_id]["lastAction"] == 3
            msg = bot_instance.send_message.call_args[0][1]
            assert "사유 : 아이디 또는 비밀번호가 올바르지 않습니다" in msg

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

    async def _start(self, bot, chat_id, valid_request, owner_id="01012345678"):
        from core.schemas import Owner

        return await bot.reservations.start(
            Owner(user_id=owner_id),
            valid_request,
            "010-1234-5678",
            "pw",
            origin="telegram",
            chat_id=chat_id,
        )

    @pytest.mark.asyncio
    async def test_cancel_func_with_reservation(
        self, bot_instance, mock_telegram_update, valid_request
    ):
        """Test /cancel command with active reservation"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        reservation = await self._start(bot_instance, chat_id, valid_request)

        await bot_instance.cancel_func(mock_telegram_update, None)

        # /cancel shows a cancel menu first, actual cancellation
        # happens via callback (cancel_all / cancel_{reservation_id}).
        assert bot_instance.reservations.get(reservation.id, chat_id=chat_id).is_active
        bot_instance.send_message.assert_called_once()
        markup = bot_instance.send_message.call_args[1]["reply_markup"]
        callbacks = [row[0].callback_data for row in markup.inline_keyboard]
        assert f"cancel_{reservation.id}" in callbacks

    @pytest.mark.asyncio
    async def test_cancel_callback_cancels_only_selected(
        self, bot_instance, valid_request, fake_launcher
    ):
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        first = await self._start(bot_instance, chat_id, valid_request)
        second = await self._start(bot_instance, chat_id, valid_request)

        await bot_instance._handle_cancel_callback(chat_id, f"cancel_{first.id}")

        assert not bot_instance.reservations.get(first.id, chat_id=chat_id).is_active
        assert bot_instance.reservations.get(second.id, chat_id=chat_id).is_active
        assert fake_launcher.cancelled == ["ref-1"]

    @pytest.mark.asyncio
    async def test_cancel_callback_cancel_all(self, bot_instance, valid_request):
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        await self._start(bot_instance, chat_id, valid_request)
        await self._start(bot_instance, chat_id, valid_request)

        await bot_instance._handle_cancel_callback(chat_id, "cancel_all")

        assert bot_instance._active_reservations(chat_id) == []
        assert "모든 예약이 취소" in bot_instance.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_start_reserve_launches_via_service(
        self, bot_instance, fake_launcher, valid_request
    ):
        """confirm_yes starts a reservation through ReservationService"""
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"].update(
            {"korailId": "010-1234-5678", "korailPw": "pw", "ownerId": "01012345678"}
        )
        bot_instance.userDict[chat_id]["trainInfo"] = {
            "depDate": valid_request.dep_date_compact,
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depTime": "0900",
            "maxDepTime": "1200",
            "trainType": "ALL",
            "specialInfo": "special_only",
        }
        bot_instance.send_message = AsyncMock()

        await bot_instance._start_reserve(chat_id, "confirm_yes")

        assert bot_instance.userDict[chat_id]["lastAction"] == 12
        spec = fake_launcher.launched[0]
        assert spec["train_type"] == "ALL"
        assert spec["seat_type"] == "special_only"
        assert spec["dep_time"] == "0900"
        reservations = bot_instance._active_reservations(chat_id)
        assert len(reservations) == 1
        assert reservations[0].origin == "telegram"

    @pytest.mark.asyncio
    async def test_start_reserve_rejects_invalid_request(
        self, bot_instance, fake_launcher
    ):
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"].update(
            {"korailId": "010-1234-5678", "korailPw": "pw", "ownerId": "01012345678"}
        )
        bot_instance.userDict[chat_id]["trainInfo"] = {
            "depDate": "20000101",
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depTime": "0900",
            "maxDepTime": "1200",
            "trainType": "KTX",
            "specialInfo": "general",
        }
        bot_instance.send_message = AsyncMock()

        await bot_instance._start_reserve(chat_id, "confirm_yes")

        assert fake_launcher.launched == []
        assert "출발일" in bot_instance.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_start_reserve_per_user_limit(
        self, bot_instance, fake_launcher, valid_request
    ):
        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.send_message = AsyncMock()
        for _ in range(3):
            await self._start(bot_instance, chat_id, valid_request)
        bot_instance.userDict[chat_id]["userInfo"].update(
            {"korailId": "010-1234-5678", "korailPw": "pw", "ownerId": "01012345678"}
        )
        bot_instance.userDict[chat_id]["trainInfo"] = {
            "depDate": valid_request.dep_date_compact,
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depTime": "0900",
            "maxDepTime": "1200",
            "trainType": "KTX",
            "specialInfo": "general",
        }

        await bot_instance._start_reserve(chat_id, "confirm_yes")

        assert len(fake_launcher.launched) == 3
        assert "최대 3개" in bot_instance.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_get_status_info(
        self, bot_instance, mock_telegram_update, valid_request
    ):
        """Test /status command handler"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["isAdmin"] = True
        bot_instance.send_message = AsyncMock()
        await self._start(bot_instance, 789012, valid_request, owner_id="01011111111")

        await bot_instance.get_status_info(mock_telegram_update, None)

        bot_instance.send_message.assert_called_once()
        args = bot_instance.send_message.call_args[0]
        assert "1개의 예약이 실행중입니다" in args[1]
        assert "010-1111-1111" in args[1]

    @pytest.mark.asyncio
    async def test_get_status_info_non_admin_sees_only_own(
        self, bot_instance, mock_telegram_update, valid_request
    ):
        """일반 사용자에게 다른 사용자의 전화번호를 보여주지 않음"""
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        await self._start(bot_instance, chat_id, valid_request, owner_id="01012345678")
        await self._start(bot_instance, 789012, valid_request, owner_id="01011111111")
        bot_instance.send_message = AsyncMock()

        await bot_instance.get_status_info(mock_telegram_update, None)

        text = bot_instance.send_message.call_args[0][1]
        assert "내 예약은 1개" in text
        assert "010-1111-1111" not in text

    @pytest.mark.asyncio
    async def test_cancel_all_command_requires_admin(
        self, bot_instance, mock_telegram_update, valid_request, services
    ):
        """/cancelall은 텔레그램·웹의 모든 예약을 취소하므로 관리자만"""
        from core.schemas import Owner

        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        await self._start(bot_instance, 789012, valid_request, owner_id="01011111111")
        bot_instance.send_message = AsyncMock()

        await bot_instance.cancel_all(mock_telegram_update, None)
        await bot_instance.get_all_users(mock_telegram_update, None)

        admin = Owner(user_id="admin", is_admin=True)
        assert len(services.reservations.list(admin, active=True, scope_all=True)) == 1
        messages = [c.args[1] for c in bot_instance.send_message.call_args_list]
        assert len(messages) == 2
        assert all("관리자만" in m for m in messages)

        bot_instance.userDict[chat_id]["userInfo"]["isAdmin"] = True
        await bot_instance.cancel_all(mock_telegram_update, None)
        assert services.reservations.list(admin, active=True, scope_all=True) == []

    @pytest.mark.asyncio
    async def test_deliver_success_notifies_chat_and_resets_state(
        self, bot_instance, valid_request, services
    ):
        from core.schemas import WorkerEvent

        chat_id = 123456
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["inProgress"] = True
        bot_instance.userDict[chat_id]["lastAction"] = 12
        bot_instance.send_message = AsyncMock()
        reservation = await self._start(bot_instance, chat_id, valid_request)
        spec_token = bot_instance.reservations.launcher.launched[0]["callback_token"]

        await bot_instance.reservations.handle_worker_event(
            WorkerEvent(
                reservation_id=reservation.id,
                token=spec_token,
                status="success",
                train_info="KTX 001 (09:00~11:30)",
            )
        )

        sent = [c[0] for c in bot_instance.send_message.call_args_list]
        assert any(
            c[0] == chat_id and "KTX 001" in c[1] and "예약에 성공" in c[1]
            for c in sent
        )
        assert bot_instance.userDict[chat_id]["lastAction"] == 0

    @pytest.mark.asyncio
    async def test_deliver_web_reservation_to_linked_chat(
        self, bot_instance, valid_request, services
    ):
        from core.schemas import Owner

        services.users.link_telegram("01012345678", 555)
        bot_instance.send_message = AsyncMock()
        reservation = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )

        await services.reservations.cancel(
            reservation.id, owner=Owner(user_id="admin", is_admin=True), source="admin"
        )

        chat, text = bot_instance.send_message.call_args[0][:2]
        assert chat == 555
        assert "[웹 예약]" in text
        assert "관리자에 의해" in text

    @pytest.mark.asyncio
    async def test_deliver_skips_when_telegram_notify_disabled(
        self, bot_instance, valid_request, services
    ):
        from core.schemas import Owner

        services.users.link_telegram("01012345678", 555)
        services.users.update("01012345678", telegram_notify=False)
        bot_instance.send_message = AsyncMock()
        reservation = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )

        await services.reservations.cancel(
            reservation.id, owner=Owner(user_id="01012345678"), source="web"
        )

        bot_instance.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_user_commands_require_admin(
        self, bot_instance, mock_telegram_update
    ):
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance.send_message = AsyncMock()
        context = Mock(args=["01055556666"])

        await bot_instance.add_registered_user(mock_telegram_update, context)

        assert bot_instance.users.get("01055556666") is None
        assert "관리자만" in bot_instance.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_admin_user_commands(self, bot_instance, mock_telegram_update):
        chat_id = 123456
        mock_telegram_update.message.chat_id = chat_id
        bot_instance._create_user(chat_id)
        bot_instance.userDict[chat_id]["userInfo"]["isAdmin"] = True
        bot_instance.send_message = AsyncMock()

        await bot_instance.add_registered_user(
            mock_telegram_update, Mock(args=["010-5555-6666", "홍길동"])
        )
        assert bot_instance.users.get("01055556666").name == "홍길동"

        await bot_instance.list_registered_users(mock_telegram_update, Mock(args=[]))
        assert "010-5555-6666 홍길동" in bot_instance.send_message.call_args[0][1]

        await bot_instance.delete_registered_user(
            mock_telegram_update, Mock(args=["01055556666"])
        )
        assert bot_instance.users.get("01055556666") is None


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
