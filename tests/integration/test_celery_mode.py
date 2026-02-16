"""
Integration tests for Celery execution mode
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import time


@pytest.mark.integration
@pytest.mark.celery
@pytest.mark.requires_redis
class TestCeleryMode:
    """Test Celery execution mode integration"""

    @pytest.mark.asyncio
    async def test_celery_reservation_flow(self, mock_redis_client, sample_user_data):
        """Test complete reservation flow in Celery mode"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", True
        ), patch(
            "telegramBot.bot.redis.Redis.from_url", return_value=mock_redis_client
        ):
            from telegramBot.bot import TelegramBot

            # Create bot instance in Celery mode
            bot = TelegramBot("test_token", enable_redis_celery=True)
            bot.send_message = AsyncMock()
            chat_id = 123456

            # Verify Celery mode is enabled
            assert bot.use_celery is True
            assert bot.redis_client is not None

            # Setup user data
            bot.userDict[chat_id] = sample_user_data.copy()

            # Mock Celery task at the source module where it's defined
            with patch("telegramBot.tasks.reservation_task") as mock_task:
                mock_task.delay = Mock(return_value=Mock(id="test-task-id"))

                # Simulate starting reservation with Celery
                task_id = bot._start_celery_task(
                    sample_user_data["trainInfo"],
                    sample_user_data["userInfo"],
                    chat_id,
                )

                # Verify task was started
                assert task_id == "test-task-id"
                mock_task.delay.assert_called_once()

            # Verify Redis state was updated
            redis_data = mock_redis_client.hgetall(f"reservation:{chat_id}")
            if redis_data:  # Only if fakeredis is available
                assert redis_data.get("status") == "started"
                assert redis_data.get("task_id") == "test-task-id"

    @pytest.mark.asyncio
    async def test_celery_task_cancellation(self, mock_redis_client, mock_celery_app):
        """Test Celery task cancellation"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", True
        ), patch(
            "telegramBot.bot.redis.Redis.from_url", return_value=mock_redis_client
        ):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=True)
            bot.celery_app = mock_celery_app
            chat_id = 123456

            # Setup running status with Celery task
            bot.runningStatus["test-task-id"] = {
                "chat_id": chat_id,
                "task_id": "test-task-id",
                "korailId": "010-1234-5678",
                "method": "celery",
            }
            bot._create_user(chat_id)

            # Cancel reservation
            success = await bot._cancel_reservation(chat_id)

            # Verify cancellation
            assert success is True
            assert "test-task-id" not in bot.runningStatus
            mock_celery_app.control.revoke.assert_called_once_with(
                "test-task-id", terminate=True
            )

    @pytest.mark.asyncio
    async def test_celery_multiple_concurrent_users(self, mock_redis_client):
        """Test multiple concurrent users with Celery mode"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", True
        ), patch(
            "telegramBot.bot.redis.Redis.from_url", return_value=mock_redis_client
        ):
            from telegramBot.bot import TelegramBot

            bot = TelegramBot("test_token", enable_redis_celery=True)
            bot.send_message = AsyncMock()

            # Create multiple users
            users = [111111, 222222, 333333]

            for user_id in users:
                bot._create_user(user_id)
                bot.runningStatus[f"task-{user_id}"] = {
                    "chat_id": user_id,
                    "task_id": f"task-{user_id}",
                    "korailId": f"010-{str(user_id)[:4]}-{str(user_id)[-4:]}",
                    "method": "celery",
                }

            # Verify all are tracked
            assert len(bot.runningStatus) == 3

            # Cancel all should work
            for user_id in users:
                with patch.object(bot.celery_app.control, "revoke"):
                    success = await bot._cancel_reservation(user_id)
                    assert success is True

    def test_celery_fallback_to_subprocess(self):
        """Test fallback to subprocess mode when Redis unavailable"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", False
        ):
            from telegramBot.bot import TelegramBot

            # Try to create bot with Celery enabled but Redis not available
            bot = TelegramBot("test_token", enable_redis_celery=True)

            # Should fall back to subprocess mode
            assert bot.use_celery is False
            assert bot.redis_client is None
            assert bot.celery_app is None

    def test_celery_redis_connection_failure(self):
        """Test handling of Redis connection failure"""
        with patch("telegramBot.bot.ApplicationBuilder"), patch(
            "telegramBot.bot.REDIS_AVAILABLE", True
        ):
            mock_redis = Mock()
            mock_redis.ping = Mock(side_effect=Exception("Connection refused"))

            with patch("telegramBot.bot.redis.Redis.from_url", return_value=mock_redis):
                from telegramBot.bot import TelegramBot

                bot = TelegramBot("test_token", enable_redis_celery=True)

                # Should fall back to subprocess mode on connection error
                assert bot.use_celery is False


@pytest.mark.integration
@pytest.mark.celery
class TestCeleryTasks:
    """Test Celery tasks"""

    def test_celery_app_configuration(self):
        """Test Celery app is configured correctly"""
        from telegramBot.tasks import app

        assert app.conf.broker_url
        assert app.conf.result_backend
        assert app.conf.task_serializer == "json"
        assert app.conf.accept_content == ["json"]

    @patch("telegramBot.tasks.ReserveHandler")
    @patch("telegramBot.tasks.requests.post")
    @patch("telegramBot.tasks.redis.Redis.from_url")
    def test_reservation_task_success(
        self, mock_redis_from_url, mock_requests_post, mock_handler_class
    ):
        """Test successful reservation task execution"""
        from telegramBot.tasks import reservation_task

        # Setup mocks
        mock_redis = Mock()
        mock_redis.hget = Mock(return_value=None)
        mock_redis.hset = Mock()
        mock_redis_from_url.return_value = mock_redis

        mock_handler = Mock()
        mock_handler.login = Mock(return_value=True)
        mock_handler.reserve_single_attempt = Mock(
            return_value={
                "success": True,
                "result": "Train KTX 001 reserved",
                "error": None,
            }
        )
        mock_handler_class.return_value = mock_handler

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        # Execute task
        chat_id = 123456
        reservation_data = {
            "korail_id": "010-1234-5678",
            "korail_pw": "password",
            "dep_date": "20250115",
            "dep_station": "서울",
            "arr_station": "부산",
            "dep_time": "090000",
            "arr_time": "1200",
            "train_type": "KTX",
            "prefer_seat_type": "general",
        }
        callback_url = "http://localhost:8390/reservation_callback"

        # Call the task directly (not using __wrapped__ which has signature issues)
        result = reservation_task(chat_id, reservation_data, callback_url)

        # Verify result
        assert result["status"] == "success"
        mock_handler.login.assert_called_once()
        mock_handler.reserve_single_attempt.assert_called()

    @patch("telegramBot.tasks.ReserveHandler")
    @patch("telegramBot.tasks.requests.post")
    @patch("telegramBot.tasks.redis.Redis.from_url")
    def test_reservation_task_login_failure(
        self, mock_redis_from_url, mock_requests_post, mock_handler_class
    ):
        """Test reservation task with login failure"""
        from telegramBot.tasks import reservation_task

        # Setup mocks
        mock_redis = Mock()
        mock_redis.hget = Mock(return_value=None)
        mock_redis.hset = Mock()
        mock_redis_from_url.return_value = mock_redis

        mock_handler = Mock()
        mock_handler.login = Mock(return_value=False)
        mock_handler_class.return_value = mock_handler

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        # Execute task
        chat_id = 123456
        reservation_data = {
            "korail_id": "010-1234-5678",
            "korail_pw": "wrong_password",
            "dep_date": "20250115",
            "dep_station": "서울",
            "arr_station": "부산",
            "dep_time": "090000",
            "arr_time": "1200",
            "train_type": "KTX",
            "prefer_seat_type": "general",
        }
        callback_url = "http://localhost:8390/reservation_callback"

        # Call the task directly (not using __wrapped__ which has signature issues)
        result = reservation_task(chat_id, reservation_data, callback_url)

        # Verify failure
        assert result["status"] == "failed"
        assert "login" in result["message"].lower()

    @patch("telegramBot.tasks.requests.post")
    def test_send_callback_success(self, mock_requests_post):
        """Test callback sending on success"""
        from telegramBot.tasks import _send_callback

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        _send_callback(
            "http://localhost:8390/reservation_callback",
            123456,
            "success",
            "Train reserved",
        )

        mock_requests_post.assert_called_once()
        call_args = mock_requests_post.call_args
        assert call_args[1]["json"]["status"] == "success"
        assert call_args[1]["json"]["train_info"] == "Train reserved"

    @patch("telegramBot.tasks.requests.post")
    def test_send_callback_failure(self, mock_requests_post):
        """Test callback sending on failure"""
        from telegramBot.tasks import _send_callback

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_requests_post.return_value = mock_response

        _send_callback(
            "http://localhost:8390/reservation_callback",
            123456,
            "failed",
            "No trains available",
        )

        mock_requests_post.assert_called_once()
        call_args = mock_requests_post.call_args
        assert call_args[1]["json"]["status"] == "failed"
        assert call_args[1]["json"]["error"] == "No trains available"

    @patch("telegramBot.tasks.requests.post")
    def test_send_callback_network_error(self, mock_requests_post):
        """Test callback handling of network errors"""
        from telegramBot.tasks import _send_callback

        mock_requests_post.side_effect = Exception("Network error")

        # Should not raise exception
        _send_callback(
            "http://localhost:8390/reservation_callback",
            123456,
            "success",
            "Train reserved",
        )
