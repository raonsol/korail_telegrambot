"""
개선된 설정 관리 - Pydantic Settings 사용
"""

from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict
from typing import Literal, Optional
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

    # Celery 워커 풀 (웹 서버의 취소 방식도 이 값을 따름)
    # threads: 프로세스 1개 + 스레드 (예약당 약 0.3MB), 협력 취소
    # prefork: 슬롯마다 프로세스 (슬롯당 약 35MB), 강제 종료로 취소
    celery_pool: Literal["threads", "prefork"] = Field(
        default="threads", alias="CELERY_POOL"
    )

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 설정 오류 메시지에 환경변수 값(비밀번호 등)이 찍히지 않도록
        hide_input_in_errors=True,
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
    # 워커 동시 실행 수 (비우면 MAX_CONCURRENT_RESERVATIONS와 같게)
    celery_concurrency: Optional[int] = Field(default=None, alias="CELERY_CONCURRENCY")

    @property
    def worker_concurrency(self) -> int:
        return self.celery_concurrency or self.max_concurrent_reservations


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
    # 최초 실행 시 사용자 DB에 등록할 전화번호 목록 (이후 사용자는 DB에서 관리)
    allow_list: str = Field(default="", alias="ALLOW_LIST")

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

    # 채널 활성화
    enable_telegram: bool = Field(default=True, alias="ENABLE_TELEGRAM")
    enable_webapp: bool = Field(default=False, alias="ENABLE_WEBAPP")

    # 웹앱 설정
    webapp_origin: str = Field(default="", alias="WEBAPP_ORIGIN")
    webapp_dist_dir: str = Field(default="", alias="WEBAPP_DIST_DIR")
    # 코레일 비밀번호 암호화 키 (임의의 긴 문자열)
    webapp_enc_key: str = Field(default="", alias="WEBAPP_ENC_KEY")
    session_ttl_hours: int = Field(default=168, alias="SESSION_TTL_HOURS")
    cookie_secure: Optional[bool] = Field(default=None, alias="COOKIE_SECURE")
    login_max_failures: int = Field(default=3, alias="LOGIN_MAX_FAILURES")
    login_lock_minutes: int = Field(default=10, alias="LOGIN_LOCK_MINUTES")

    # 예약 설정
    max_reservations_per_user: int = Field(default=3, alias="MAX_RESERVATIONS_PER_USER")
    reservation_retention_days: int = Field(
        default=30, alias="RESERVATION_RETENTION_DAYS"
    )
    # 워커가 예약 상태를 보고할 URL (비어 있으면 로컬 포트로 자동 설정)
    internal_callback_url: str = Field(default="", alias="INTERNAL_CALLBACK_URL")

    # Web Push (VAPID)
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(
        default="mailto:admin@example.com", alias="VAPID_SUBJECT"
    )

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

    @property
    def local_port(self) -> int:
        """로컬 실행 포트 (dev: 8390, prod: 8391)"""
        return 8390 if self.is_dev else 8391

    @property
    def callback_url(self) -> str:
        """워커 콜백 URL"""
        if self.internal_callback_url:
            return self.internal_callback_url
        return f"http://127.0.0.1:{self.local_port}/internal/events"

    @property
    def secure_cookies(self) -> bool:
        """세션 쿠키에 Secure 속성을 붙일지 여부"""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return not self.is_dev

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)


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
