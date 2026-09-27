"""FastAPI 앱 조립

- 텔레그램 webhook (``ENABLE_TELEGRAM``)
- 웹앱 API + PWA 정적 파일 (``ENABLE_WEBAPP``)
- 워커 콜백 ``/internal/events`` (항상)
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from core.services import Services, housekeeping_loop

from . import (
    routes_admin,
    routes_auth,
    routes_events,
    routes_internal,
    routes_push,
    routes_reservations,
    routes_stations,
)
from .errors import register_error_handlers
from .static import mount_webapp, resolve_dist_dir

logger = logging.getLogger(__name__)


async def setup_telegram_webhook(bot, webhook_base_url: str) -> None:
    if not webhook_base_url:
        logger.error("Webhook URL not found in environment variables")
        raise ValueError("Webhook URL is required")

    webhook_url = f"{webhook_base_url}/message"
    logger.info(f"Setting webhook to {webhook_url}")
    try:
        if await bot.set_webhook(url=webhook_url):
            logger.info("Webhook set successfully")
        else:
            logger.error("Failed to set webhook")
        webhook_info = await bot.app.bot.get_webhook_info()
        logger.info(f"Current webhook info: {webhook_info}")
    except Exception as e:
        # webhook 설정 실패해도 서버는 계속 실행되도록 함
        logger.error(f"Error setting webhook: {e}")


def create_app(
    settings,
    services: Services,
    bot=None,
    *,
    run_housekeeping: bool = True,
    dist_dir: Optional[str] = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.init_storage()
        services.reservations.bind_loop()
        housekeeping = (
            asyncio.create_task(housekeeping_loop(services))
            if run_housekeeping
            else None
        )
        try:
            if bot is not None:
                await setup_telegram_webhook(bot, settings.webhook_url_by_env)
                async with bot.app:
                    await bot.app.start()
                    logger.info("Bot application started")
                    # 알림 채널(텔레그램)이 준비된 뒤 정리해야 사용자에게 전달됨
                    await services.reservations.abort_interrupted()
                    yield
                    logger.info("Shutting down bot application")
                    await bot.app.stop()
            else:
                await services.reservations.abort_interrupted()
                yield
        finally:
            if housekeeping:
                housekeeping.cancel()
                with suppress(asyncio.CancelledError):
                    await housekeeping

    app = FastAPI(
        title="Korail Reservation",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.enable_webapp else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_webapp else None,
    )
    app.state.services = services
    app.state.bot = bot
    register_error_handlers(app)

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "korail_telegrambot"}

    if bot is not None:
        from telegram import Update

        @app.post("/message")
        async def process_update(request: Request):
            update = Update.de_json(await request.json(), bot.app.bot)
            await bot.app.update_queue.put(update)
            return Response(status_code=status.HTTP_200_OK)

    app.include_router(routes_internal.router)

    if settings.enable_webapp:
        if settings.webapp_origin:
            # 프론트를 다른 오리진에서 서빙하는 경우에만 필요 (기본은 같은 오리진)
            app.add_middleware(
                CORSMiddleware,
                allow_origins=[settings.webapp_origin],
                allow_credentials=True,
                allow_methods=["GET", "POST", "PATCH", "DELETE"],
                allow_headers=["Content-Type", "X-CSRF-Token"],
            )
        for module in (
            routes_auth,
            routes_reservations,
            routes_stations,
            routes_events,
            routes_push,
            routes_admin,
        ):
            app.include_router(module.router)
        mount_webapp(app, resolve_dist_dir(dist_dir or settings.webapp_dist_dir))

    return app
