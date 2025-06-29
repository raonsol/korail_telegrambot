"""
개선된 설정 관리 - Pydantic Settings 사용
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # Telegram Bot 설정 (기존 BOTTOKEN과 호환)
    telegram_token: str = Field(alias="BOTTOKEN")
    telegram_token_dev: Optional[str] = Field(default=None, alias="BOTTOKEN_DEV")
    webhook_url: str
    webhook_url_dev: Optional[str] = None

    # Korail 설정 (기존 USERID, USERPW와 호환)
    admin_korail_id: Optional[str] = Field(default=None, alias="ADMIN_KORAIL_ID")
    admin_korail_pw: Optional[str] = Field(default=None, alias="ADMIN_KORAIL_PW")
    admin_password: str = Field(alias="ADMINPW")
    allow_list: str

    # Redis 설정
    redis_url: str = "redis://localhost:6379"
    redis_db: int = 0

    # Database 설정
    database_url: str = "sqlite:///./korail_bot.db"

    # Celery 설정
    celery_broker: str = "redis://localhost:6379"
    celery_result_backend: str = "redis://localhost:6379"

    # 애플리케이션 설정
    debug: bool = False
    is_dev: bool = False  # 개발 환경 여부
    log_level: str = "INFO"
    max_concurrent_reservations: int = 10
    reservation_timeout: int = 3600  # 1시간

    # 보안 설정
    secret_key: str = "your-secret-key-here"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 추가 필드 무시

    @property
    def bot_token(self) -> str:
        """환경에 따른 봇 토큰 반환"""
        return (
            self.telegram_token_dev
            if self.is_dev and self.telegram_token_dev
            else self.telegram_token
        )

    @property
    def webhook_url_by_env(self) -> str:
        """환경에 따른 웹훅 URL 반환"""
        return (
            self.webhook_url_dev
            if self.is_dev and self.webhook_url_dev
            else self.webhook_url
        )


# 싱글톤 설정 인스턴스
settings = Settings()
