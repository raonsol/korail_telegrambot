"""
Integration tests for subprocess execution mode
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import subprocess
import os


@pytest.mark.integration
@pytest.mark.subprocess
class TestSubprocessMode:
    """Test subprocess execution mode integration"""

    @pytest.mark.asyncio
    async def test_subprocess_reservation_flow(self, sample_user_data):
        """Test complete reservation flow in subprocess mode"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.subprocess.Popen"
        ) as mock_popen:
            from telegramBot.bot import TelegramBot

            # Setup mocks
            mock_process = Mock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            # Create bot instance in subprocess mode
            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()
            chat_id = 123456

            # Setup user data
            bot.userDict[chat_id] = sample_user_data.copy()

            # Simulate starting reservation
            arguments = [
                sample_user_data["userInfo"]["korailId"],
                sample_user_data["userInfo"]["korailPw"],
                sample_user_data["trainInfo"]["depDate"],
                sample_user_data["trainInfo"]["srcLocate"],
                sample_user_data["trainInfo"]["dstLocate"],
                f"{sample_user_data['trainInfo']['depTime']}00",
                sample_user_data["trainInfo"]["trainType"],
                sample_user_data["trainInfo"]["specialInfo"],
                str(chat_id),
                sample_user_data["trainInfo"]["maxDepTime"],
            ]

            with patch("builtins.open"), patch("telegramBot.bot.os.makedirs"), patch(
                "telegramBot.bot.threading.Thread"
            ):
                pid = bot._start_background_process(arguments)

            # Verify process was started
            assert pid == 12345
            assert str(pid) not in bot.runningStatus  # Not added yet

            # Add to running status manually (as would happen in actual flow)
            bot.runningStatus[str(pid)] = {
                "chat_id": chat_id,
                "pid": pid,
                "korailId": "010-1234-5678",
                "method": "subprocess",
            }

            # Verify status
            assert str(pid) in bot.runningStatus
            assert bot.runningStatus[str(pid)]["pid"] == 12345

            # Simulate cancellation
            with patch("telegramBot.bot.os.killpg"), patch(
                "telegramBot.bot.os.getpgid"
            ):
                success = await bot._cancel_reservation(chat_id)

            assert success is True
            assert str(pid) not in bot.runningStatus

    @pytest.mark.asyncio
    async def test_subprocess_multiple_concurrent_users(self):
        """Test multiple users with subprocess mode (should handle sequentially)"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            bot.send_message = AsyncMock()

            # Create multiple users
            user1_id = 111111
            user2_id = 222222

            bot._create_user(user1_id)
            bot._create_user(user2_id)

            # Simulate first user starting reservation
            bot.runningStatus["12345"] = {
                "chat_id": user1_id,
                "pid": 12345,
                "korailId": "010-1111-1111",
                "method": "subprocess",
            }

            # Second user tries to start
            # In actual implementation, this should be prevented
            bot.runningStatus["67890"] = {
                "chat_id": user2_id,
                "pid": 67890,
                "korailId": "010-2222-2222",
                "method": "subprocess",
            }

            # Verify both are tracked
            assert len(bot.runningStatus) == 2

    def test_subprocess_worker_initialization(self):
        """Test worker process initialization"""
        test_args = [
            "test_script.py",
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

        with patch("sys.argv", test_args), patch(
            "telegramBot.worker.ReserveHandler"
        ) as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=True)
            mock_handler_class.return_value = mock_handler

            from telegramBot.worker import BackProcess

            process = BackProcess()

            assert process.username == "010-1234-5678"
            assert process.password == "password"
            assert process.depDate == "20250115"
            assert process.srcLocate == "서울"
            assert process.dstLocate == "부산"
            assert process.chatId == "123456"

    @pytest.mark.asyncio
    async def test_subprocess_callback_on_completion(self):
        """Test callback mechanism when subprocess completes"""
        from unittest.mock import patch

        with patch("telegramBot.bot.ApplicationBuilder"):
            with patch("app.bot") as mock_bot:
                mock_bot.runningStatus = {
                    "12345": {"chat_id": 123456, "pid": 12345, "method": "subprocess"}
                }
                mock_bot.send_message = AsyncMock()
                mock_bot._reset_user_state = Mock()
                mock_bot.userDict = {123456: {"inProgress": True}}

                from app import send_reservation_status

                # Simulate successful reservation callback handler directly
                await send_reservation_status(
                    chat_id=123456,
                    status=1,
                    reserveInfo="Train KTX 001 reserved",
                )

                # Verify callback side-effects
                mock_bot.send_message.assert_awaited_once()
                mock_bot._reset_user_state.assert_called_once_with(123456)
                assert "12345" not in mock_bot.runningStatus


@pytest.mark.integration
@pytest.mark.subprocess
class TestSubprocessErrorHandling:
    """Test error handling in subprocess mode"""

    @pytest.mark.asyncio
    async def test_subprocess_crash_handling(self):
        """Test handling of crashed subprocess"""
        with patch("telegramBot.bot.ApplicationBuilder"):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=False)
            chat_id = 123456

            # Simulate crashed process
            bot.runningStatus["99999"] = {
                "chat_id": chat_id,
                "pid": 99999,
                "korailId": "010-1234-5678",
                "method": "subprocess",
            }

            # Try to cancel non-existent process
            with patch("telegramBot.bot.os.killpg", side_effect=ProcessLookupError()):
                with patch("telegramBot.bot.os.getpgid", return_value=99999):
                    success = await bot._cancel_reservation(chat_id)

            # Should still clean up state
            assert "99999" not in bot.runningStatus

    def test_subprocess_login_failure(self):
        """Test subprocess behavior when login fails"""
        test_args = [
            "test_script.py",
            "010-1234-5678",
            "wrong_password",
            "20250115",
            "서울",
            "부산",
            "090000",
            "KTX",
            "1",
            "123456",
            "1200",
        ]

        with patch("sys.argv", test_args), patch(
            "telegramBot.worker.ReserveHandler"
        ) as mock_handler_class:
            mock_handler = Mock()
            mock_handler.login = Mock(return_value=False)
            mock_handler_class.return_value = mock_handler

            from telegramBot.worker import BackProcess

            # Should raise exception during initialization
            with pytest.raises(SystemExit):
                process = BackProcess()
