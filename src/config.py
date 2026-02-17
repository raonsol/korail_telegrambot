"""
개선된 설정 관리 - Pydantic Settings 사용
"""

from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict
from typing import Optional
import os


class BaseAppSettings(BaseSettings):
    """Common settings shared by all services"""

    # Infrastructure settings
    redis_url: str = "redis://localhost:6379"
    redis_db: int = 0
    database_url: str = "sqlite:///./korail_bot.db"

    # Application settings
    debug: bool = False
    log_level: str = "INFO"

    model_config = ConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


class CelerySettings(BaseAppSettings):
    """Settings for Celery workers - only what they actually need"""

    # Celery configuration
    celery_broker: str = Field(default="redis://localhost:6379", alias="CELERY_BROKER")
    celery_result_backend: str = Field(
        default="redis://localhost:6379", alias="CELERY_RESULT_BACKEND"
    )

    # Worker settings
    max_concurrent_reservations: int = 10
    reservation_timeout: int = 3600  # 1시간


class WebSettings(BaseAppSettings):
    """Settings for web service - bot and API related"""

    # Telegram Bot 설정 - 환경에 따라 자동 선택
    # Local execution (IS_DEV=true): BOTTOKEN_DEV, WEBHOOK_URL_DEV
    # Docker/Production: BOTTOKEN, WEBHOOK_URL
    telegram_token: str = Field(default="")
    webhook_url: str = Field(default="")

    # Korail 설정 (기존 USERID, USERPW와 호환)
    admin_korail_id: Optional[str] = Field(default=None, alias="ADMIN_KORAIL_ID")
    admin_korail_pw: Optional[str] = Field(default=None, alias="ADMIN_KORAIL_PW")
    admin_password: str = Field(alias="ADMINPW")
    allow_list: str

    # Celery configuration (needed for task dispatch)
    celery_broker: str = Field(default="redis://localhost:6379", alias="CELERY_BROKER")
    celery_result_backend: str = Field(
        default="redis://localhost:6379", alias="CELERY_RESULT_BACKEND"
    )

    # Worker settings
    max_concurrent_reservations: int = 10
    reservation_timeout: int = 3600  # 1시간

    # 공공데이터 API
    datagov_api_key: str = Field(default="", alias="DATAGOV_API_KEY")

    # 보안 설정
    secret_key: str = "your-secret-key-here"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 환경 변수에서 직접 토큰 선택
        is_dev = os.getenv("IS_DEV", "false").lower() == "true"

        if is_dev:
            # Local execution - use dev bot
            self.telegram_token = os.getenv("BOTTOKEN_DEV", "")
            self.webhook_url = os.getenv("WEBHOOK_URL_DEV", "")
        else:
            # Docker/Production - use production bot
            self.telegram_token = os.getenv("BOTTOKEN", "")
            self.webhook_url = os.getenv("WEBHOOK_URL", "")

    @property
    def bot_token(self) -> str:
        """봇 토큰 반환"""
        return self.telegram_token

    @property
    def webhook_url_by_env(self) -> str:
        """웹훅 URL 반환"""
        return self.webhook_url

    @property
    def is_dev(self) -> bool:
        """개발 모드 여부 반환"""
        return os.getenv("IS_DEV", "false").lower() == "true"


# Settings instances - use appropriate one based on service type
celery_settings = CelerySettings()
web_settings = WebSettings()


# Backward compatibility - determine which settings to use based on context
def get_settings():
    """Return appropriate settings based on service context"""
    # Check if running in Celery worker context
    if os.getenv("CELERY_WORKER_NAME") or "celery" in os.getenv("_", "").lower():
        return celery_settings
    return web_settings


# Default settings for backward compatibility
settings = get_settings()
