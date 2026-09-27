import os
import logging

from config import web_settings as settings
from core.services import build_services
from web.factory import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Check if Celery should be used via environment variable
use_celery = os.getenv("USE_CELERY", "false").lower() == "true"

if use_celery:
    logger.info("Using Celery for background tasks")
else:
    logger.info("Using subprocess for background tasks")

services = build_services(settings, use_celery)

bot = None
if settings.enable_telegram:
    from telegramBot.bot import TelegramBot

    bot_token = settings.bot_token
    if not bot_token:
        logger.error("Bot token not found in environment variables")
        raise ValueError("Bot token is required")
    logger.info(f"Using bot token: {bot_token[:10]}...")

    bot = TelegramBot(bot_token, services)
    services.notifier.add(bot)
    services.auth.alert = bot.broadcast_message

if not (settings.enable_telegram or settings.enable_webapp):
    logger.warning("Both ENABLE_TELEGRAM and ENABLE_WEBAPP are disabled")

app = create_app(settings, services, bot)


if __name__ == "__main__":
    # Server will be run using the make run command, not inside app.py
    pass
