"""
Unit tests for FastAPI application (app.py)
"""

import importlib
from unittest.mock import Mock, AsyncMock, patch

import pytest


class DummyRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def app_with_mock_bot():
    with patch("telegramBot.bot.ApplicationBuilder"):
        import app as app_module

    mock_bot = Mock()
    mock_bot.app = Mock()
    mock_bot.app.bot = Mock()
    mock_bot.app.update_queue = Mock()
    mock_bot.app.update_queue.put = AsyncMock()
    mock_bot.send_message = AsyncMock()
    mock_bot._reset_user_state = Mock()
    mock_bot.runningStatus = {}
    mock_bot.userDict = {}

    app_module.bot = mock_bot
    return app_module, mock_bot


@pytest.mark.unit
class TestFastAPIEndpoints:
    @pytest.mark.asyncio
    async def test_health_check_endpoint(self, app_with_mock_bot):
        app_module, _ = app_with_mock_bot

        response = await app_module.health_check()

        assert response["status"] == "healthy"
        assert response["service"] == "korail_telegrambot"

    @pytest.mark.asyncio
    async def test_message_endpoint(self, app_with_mock_bot):
        app_module, mock_bot = app_with_mock_bot

        telegram_update = {
            "update_id": 123456,
            "message": {
                "message_id": 1,
                "from": {"id": 123456, "first_name": "Test", "is_bot": False},
                "chat": {"id": 123456, "type": "private"},
                "text": "Hello",
                "date": 1704067200,
            },
        }

        with patch.object(app_module.Update, "de_json", return_value=Mock()):
            response = await app_module.process_update(
                DummyRequest(payload=telegram_update)
            )

        assert response.status_code == 200
        mock_bot.app.update_queue.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_completion_endpoint_success(self, app_with_mock_bot):
        app_module, mock_bot = app_with_mock_bot
        chat_id = 123456

        mock_bot.runningStatus = {"12345": {"chat_id": chat_id, "pid": 12345}}
        mock_bot.userDict = {chat_id: {"inProgress": True}}

        await app_module.send_reservation_status(
            chat_id=chat_id,
            status=1,
            reserveInfo="Train KTX 001 reserved",
        )

        assert "12345" not in mock_bot.runningStatus
        mock_bot.send_message.assert_awaited_once()
        mock_bot._reset_user_state.assert_called_once_with(chat_id)

    @pytest.mark.asyncio
    async def test_completion_endpoint_not_in_queue(self, app_with_mock_bot):
        app_module, mock_bot = app_with_mock_bot

        mock_bot.runningStatus = {}

        response = await app_module.send_reservation_status(
            chat_id=999999,
            status=1,
            reserveInfo="Train reserved",
        )

        assert response is None
        mock_bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reservation_callback_success(self, app_with_mock_bot):
        app_module, mock_bot = app_with_mock_bot
        chat_id = 123456

        mock_bot.runningStatus = {
            "test-task-id": {"chat_id": chat_id, "task_id": "test-task-id"}
        }
        mock_bot.userDict = {chat_id: {"inProgress": True}}

        callback_data = {
            "user_id": str(chat_id),
            "status": "success",
            "train_info": "KTX 001 (09:00~11:30)",
            "task_id": "test-task-id",
        }

        response = await app_module.handle_reservation_callback(
            DummyRequest(payload=callback_data)
        )

        assert response.status_code == 200
        assert "test-task-id" not in mock_bot.runningStatus
        mock_bot.send_message.assert_awaited_once()
        mock_bot._reset_user_state.assert_called_once_with(chat_id)

    @pytest.mark.asyncio
    async def test_reservation_callback_failed(self, app_with_mock_bot):
        app_module, mock_bot = app_with_mock_bot
        chat_id = 123456

        mock_bot.runningStatus = {
            "test-task-id": {"chat_id": chat_id, "task_id": "test-task-id"}
        }

        callback_data = {
            "user_id": str(chat_id),
            "status": "failed",
            "error": "No trains available",
            "task_id": "test-task-id",
        }

        response = await app_module.handle_reservation_callback(
            DummyRequest(payload=callback_data)
        )

        assert response.status_code == 200
        assert "test-task-id" not in mock_bot.runningStatus
        mock_bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reservation_callback_missing_user_id(self, app_with_mock_bot):
        app_module, _ = app_with_mock_bot

        callback_data = {"status": "success", "train_info": "KTX 001"}

        response = await app_module.handle_reservation_callback(
            DummyRequest(payload=callback_data)
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_reservation_callback_invalid_data(self, app_with_mock_bot):
        app_module, _ = app_with_mock_bot

        response = await app_module.handle_reservation_callback(
            DummyRequest(error=ValueError("invalid json"))
        )

        assert response.status_code == 500


@pytest.mark.unit
class TestAppConfiguration:
    def test_cors_middleware_configured(self, app_with_mock_bot):
        app_module, _ = app_with_mock_bot

        middleware_classes = [m.cls.__name__ for m in app_module.app.user_middleware]
        assert "CORSMiddleware" in middleware_classes

    @pytest.mark.skip(
        reason="Bot token validation happens at module import, difficult to test in isolation"
    )
    def test_bot_token_validation(self):
        pass

    def test_use_celery_environment_variable(self):
        with patch.dict("os.environ", {"USE_CELERY": "true"}):
            with patch("telegramBot.bot.ApplicationBuilder"):
                with patch("telegramBot.bot.TelegramBot") as mock_telegram_bot:
                    import app

                    importlib.reload(app)

                    assert mock_telegram_bot.call_args[1]["enable_redis_celery"] is True


@pytest.mark.unit
class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_webhook_setup(self):
        with patch("telegramBot.bot.ApplicationBuilder"):
            from app import lifespan
            from fastapi import FastAPI

        mock_app = FastAPI()

        with patch("app.bot") as mock_bot:
            mock_bot.set_webhook = AsyncMock(return_value=True)
            mock_bot.app = Mock()
            mock_bot.app.bot = Mock()
            mock_bot.app.bot.get_webhook_info = AsyncMock(
                return_value={"url": "http://test.example.com"}
            )
            mock_bot.app.start = AsyncMock()
            mock_bot.app.stop = AsyncMock()
            mock_bot.app.__aenter__ = AsyncMock(return_value=mock_bot.app)
            mock_bot.app.__aexit__ = AsyncMock()

            with patch("app.settings") as mock_settings:
                mock_settings.webhook_url_by_env = "http://test.example.com"

                async with lifespan(mock_app):
                    mock_bot.set_webhook.assert_called_once()

                mock_bot.app.stop.assert_called_once()
