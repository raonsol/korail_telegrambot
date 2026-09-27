"""
Unit tests for FastAPI application assembly (app.py / web.factory)
"""

import asyncio
import importlib
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from web.factory import create_app


def _mock_bot():
    bot = Mock()
    bot.app = Mock()
    bot.app.bot = Mock()
    bot.app.bot.get_webhook_info = AsyncMock(return_value={"url": "http://x"})
    bot.app.update_queue = Mock()
    bot.app.update_queue.put = AsyncMock()
    bot.app.start = AsyncMock()
    bot.app.stop = AsyncMock()
    bot.app.__aenter__ = AsyncMock(return_value=bot.app)
    bot.app.__aexit__ = AsyncMock(return_value=None)
    bot.set_webhook = AsyncMock(return_value=True)
    return bot


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


@pytest.mark.unit
class TestFastAPIEndpoints:
    @pytest.mark.asyncio
    async def test_health_check_endpoint(self, test_settings, services):
        app = create_app(test_settings, services, run_housekeeping=False)
        async with _client(app) as c:
            response = await c.get("/health")

        assert response.json() == {"status": "healthy", "service": "korail_telegrambot"}

    @pytest.mark.asyncio
    async def test_message_endpoint(self, test_settings, services):
        bot = _mock_bot()
        app = create_app(test_settings, services, bot, run_housekeeping=False)

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
        with patch("telegram.Update.de_json", return_value=Mock()):
            async with _client(app) as c:
                response = await c.post("/message", json=telegram_update)

        assert response.status_code == 200
        bot.app.update_queue.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_endpoint_absent_without_telegram(
        self, test_settings, services
    ):
        app = create_app(test_settings, services, None, run_housekeeping=False)
        async with _client(app) as c:
            response = await c.post("/message", json={})
        assert response.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_legacy_unauthenticated_callbacks_removed(
        self, test_settings, services
    ):
        """/completion, /reservation_callback은 인증이 없어 제거됨"""
        app = create_app(test_settings, services, run_housekeeping=False)
        async with _client(app) as c:
            assert (
                await c.post("/completion/1?status=1&reserveInfo=x")
            ).status_code in (
                404,
                405,
            )
            assert (await c.post("/reservation_callback", json={})).status_code in (
                404,
                405,
            )

    @pytest.mark.asyncio
    async def test_internal_events_validates_payload(self, test_settings, services):
        app = create_app(test_settings, services, run_housekeeping=False)
        async with _client(app) as c:
            response = await c.post("/internal/events", json={"status": "success"})
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION"

    @pytest.mark.asyncio
    async def test_internal_events_unknown_reservation(self, test_settings, services):
        app = create_app(test_settings, services, run_housekeeping=False)
        async with _client(app) as c:
            response = await c.post(
                "/internal/events",
                json={"reservation_id": "nope", "token": "t", "status": "success"},
            )
        assert response.status_code == 404


@pytest.mark.unit
class TestAppConfiguration:
    def test_no_wildcard_cors(self, test_settings, services):
        app = create_app(test_settings, services, run_housekeeping=False)
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" not in middleware_classes

    def test_cors_only_for_configured_origin(self, test_settings, services):
        settings = test_settings.model_copy(
            update={"webapp_origin": "https://korail.example.com"}
        )
        app = create_app(settings, services, run_housekeeping=False)
        cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        assert cors[0].kwargs["allow_origins"] == ["https://korail.example.com"]

    def test_webapp_routes_disabled(self, test_settings, services):
        settings = test_settings.model_copy(update={"enable_webapp": False})
        app = create_app(settings, services, run_housekeeping=False)
        paths = {r.path for r in app.routes}
        assert "/internal/events" in paths
        assert not any(p.startswith("/api/") for p in paths)

    def test_use_celery_environment_variable(self):
        with patch.dict("os.environ", {"USE_CELERY": "true"}):
            with patch("telegramBot.bot.ApplicationBuilder"), patch(
                "core.services.build_services"
            ) as mock_build:
                import app

                importlib.reload(app)

                assert mock_build.call_args[0][1] is True

    def test_app_module_wires_bot_as_notification_channel(self):
        with patch.dict("os.environ", {"USE_CELERY": "false"}):
            with patch("telegramBot.bot.ApplicationBuilder"):
                import app

                importlib.reload(app)

        assert app.bot in app.services.notifier.channels
        assert app.services.auth.alert == app.bot.broadcast_message


@pytest.mark.unit
class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_webhook_setup(self, test_settings, services):
        bot = _mock_bot()
        app = create_app(test_settings, services, bot, run_housekeeping=False)

        async with app.router.lifespan_context(app):
            bot.set_webhook.assert_awaited_once_with(
                url=f"{test_settings.webhook_url_by_env}/message"
            )
            bot.app.start.assert_awaited_once()

        bot.app.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_without_telegram(self, test_settings, services):
        app = create_app(test_settings, services, None, run_housekeeping=False)
        async with app.router.lifespan_context(app):
            # 저장소 초기화 + ALLOW_LIST 시드
            assert services.users.is_allowed("01012345678")

    @pytest.mark.asyncio
    async def test_lifespan_runs_housekeeping(self, test_settings, services):
        with patch(
            "web.factory.housekeeping_loop", new=AsyncMock(return_value=None)
        ) as loop:
            app = create_app(test_settings, services, None)
            async with app.router.lifespan_context(app):
                await asyncio.sleep(0)  # let the background task start
        loop.assert_awaited_once_with(services)
