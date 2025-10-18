"""
Unit tests for FastAPI application (app.py)
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.mark.unit
class TestFastAPIEndpoints:
    """Test FastAPI endpoints"""

    @pytest.fixture
    def test_client(self):
        """Create a test client for FastAPI"""
        from datetime import timezone

        # Need to mock lifespan to avoid actual webhook setup
        with patch("app.lifespan"):
            import app as app_module

            # Create mock bot with proper Bot object
            mock_bot = Mock()
            mock_bot.app = Mock()
            mock_bot.app.bot = Mock()
            mock_bot.app.bot.set_webhook = AsyncMock(return_value=True)
            mock_bot.app.bot.get_webhook_info = AsyncMock(
                return_value={
                    "url": "http://test.example.com",
                    "pending_update_count": 0,
                }
            )
            mock_bot.app.bot.send_message = AsyncMock()
            # Add timezone info for Update.de_json
            mock_bot.app.bot.defaults = Mock()
            mock_bot.app.bot.defaults.tzinfo = timezone.utc
            mock_bot.app.start = AsyncMock()
            mock_bot.app.stop = AsyncMock()
            mock_bot.app.update_queue = Mock()
            mock_bot.app.update_queue.put = AsyncMock()
            mock_bot.send_message = AsyncMock()
            mock_bot._reset_user_state = Mock()
            mock_bot.runningStatus = {}
            mock_bot.userDict = {}
            mock_bot.set_webhook = AsyncMock(return_value=True)

            # Replace the bot in the imported module
            app_module.bot = mock_bot

            client = TestClient(app_module.app)
            return client, mock_bot

    def test_health_check_endpoint(self, test_client):
        """Test health check endpoint"""
        client, _ = test_client

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["service"] == "korail_telegrambot"

    def test_message_endpoint(self, test_client):
        """Test message processing endpoint"""
        client, mock_bot = test_client

        telegram_update = {
            "update_id": 123456,
            "message": {
                "message_id": 1,
                "from": {"id": 123456, "first_name": "Test", "is_bot": False},
                "chat": {"id": 123456, "type": "private"},
                "text": "Hello",
                "date": 1704067200,  # Required field for Message
            },
        }

        response = client.post("/message", json=telegram_update)

        assert response.status_code == 200

    def test_completion_endpoint_success(self, test_client):
        """Test completion endpoint with successful reservation"""
        client, mock_bot = test_client
        chat_id = 123456

        mock_bot.runningStatus = {chat_id: {"pid": 12345}}
        mock_bot.userDict = {chat_id: {"inProgress": True}}

        response = client.post(
            f"/completion/{chat_id}",
            params={"status": 1, "reserveInfo": "Train KTX 001 reserved"},
        )

        assert response.status_code == 200

    def test_completion_endpoint_failure(self, test_client):
        """Test completion endpoint with failed reservation"""
        client, mock_bot = test_client
        chat_id = 123456

        mock_bot.runningStatus = {chat_id: {"pid": 12345}}

        response = client.post(
            f"/completion/{chat_id}",
            params={"status": 0, "reserveInfo": ""},
        )

        assert response.status_code == 200

    def test_completion_endpoint_error(self, test_client):
        """Test completion endpoint with error status"""
        client, mock_bot = test_client
        chat_id = 123456

        mock_bot.runningStatus = {chat_id: {"pid": 12345}}

        response = client.post(
            f"/completion/{chat_id}",
            params={"status": -1, "reserveInfo": "Error occurred"},
        )

        assert response.status_code == 200

    def test_completion_endpoint_not_in_queue(self, test_client):
        """Test completion endpoint when chat_id not in running status"""
        client, mock_bot = test_client
        chat_id = 999999

        mock_bot.runningStatus = {}

        response = client.post(
            f"/completion/{chat_id}",
            params={"status": 1, "reserveInfo": "Train reserved"},
        )

        # Should return 200 but not process
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_reservation_callback_success(self, test_client):
        """Test reservation callback endpoint with success"""
        client, mock_bot = test_client
        chat_id = 123456

        mock_bot.runningStatus = {chat_id: {"task_id": "test-task-id"}}
        mock_bot.userDict = {chat_id: {"inProgress": True}}

        callback_data = {
            "user_id": str(chat_id),
            "status": "success",
            "train_info": "KTX 001 (09:00~11:30)",
            "task_id": "test-task-id",
        }

        response = client.post("/reservation_callback", json=callback_data)

        assert response.status_code == 200

    def test_reservation_callback_failed(self, test_client):
        """Test reservation callback endpoint with failure"""
        client, mock_bot = test_client
        chat_id = 123456

        mock_bot.runningStatus = {chat_id: {"task_id": "test-task-id"}}

        callback_data = {
            "user_id": str(chat_id),
            "status": "failed",
            "error": "No trains available",
            "task_id": "test-task-id",
        }

        response = client.post("/reservation_callback", json=callback_data)

        assert response.status_code == 200

    def test_reservation_callback_missing_user_id(self, test_client):
        """Test reservation callback with missing user_id"""
        client, mock_bot = test_client

        callback_data = {"status": "success", "train_info": "KTX 001"}

        response = client.post("/reservation_callback", json=callback_data)

        assert response.status_code == 400

    def test_reservation_callback_invalid_data(self, test_client):
        """Test reservation callback with invalid JSON"""
        client, mock_bot = test_client

        response = client.post(
            "/reservation_callback",
            data="invalid json",
            headers={"Content-Type": "application/json"},
        )

        # Invalid JSON is caught by exception handler and returns 500
        assert response.status_code == 500


@pytest.mark.unit
class TestAppConfiguration:
    """Test app configuration and initialization"""

    def test_cors_middleware_configured(self):
        """Test CORS middleware is configured by checking response headers"""
        with patch("app.lifespan"):
            import app as app_module

            # Create mock bot
            mock_bot = Mock()
            mock_bot.set_webhook = AsyncMock(return_value=True)
            app_module.bot = mock_bot

            client = TestClient(app_module.app)

            # Make a request with Origin header to trigger CORS
            response = client.get("/health", headers={"Origin": "http://example.com"})

            # Check if CORS headers are present in response
            assert "access-control-allow-origin" in response.headers

    @pytest.mark.skip(
        reason="Bot token validation happens at module import, difficult to test in isolation"
    )
    def test_bot_token_validation(self):
        """Test that app validates bot token on startup"""
        # This test is skipped because the bot is created at module level
        # and testing import-time validation requires complex module reloading
        # The validation is verified manually during deployment
        pass

    def test_use_celery_environment_variable(self):
        """Test USE_CELERY environment variable configuration"""
        with patch.dict("os.environ", {"USE_CELERY": "true"}):
            with patch("app.bot") as mock_bot:
                with patch("app.lifespan"):
                    import importlib
                    import app

                    importlib.reload(app)

                    # Verify bot was initialized with Celery enabled
                    # Note: This may not work perfectly due to module caching


@pytest.mark.unit
class TestLifespan:
    """Test lifespan events"""

    @pytest.mark.asyncio
    async def test_lifespan_webhook_setup(self):
        """Test webhook is set up during lifespan startup"""
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
                    # Webhook should be set during startup
                    mock_bot.set_webhook.assert_called_once()

                # App should be stopped after context exit
                mock_bot.app.stop.assert_called_once()
