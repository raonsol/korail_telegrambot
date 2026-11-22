"""
Unit tests for configuration management (config.py)
"""

import pytest


class TestWebSettings:
    """Test WebSettings configuration"""

    def test_web_settings_dev_mode(self, monkeypatch):
        """Test WebSettings in development mode"""
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_token_123")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.is_dev is True
        assert settings.bot_token == "dev_token_123"
        assert settings.webhook_url_by_env == "http://dev.example.com"
        assert settings.telegram_token == "dev_token_123"
        assert settings.webhook_url == "http://dev.example.com"

    def test_web_settings_production_mode(self, monkeypatch):
        """Test WebSettings in production mode"""
        monkeypatch.setenv("IS_DEV", "false")
        monkeypatch.setenv("BOTTOKEN", "prod_token_456")
        monkeypatch.setenv("WEBHOOK_URL", "http://prod.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.is_dev is False
        assert settings.bot_token == "prod_token_456"
        assert settings.webhook_url_by_env == "http://prod.example.com"
        assert settings.telegram_token == "prod_token_456"
        assert settings.webhook_url == "http://prod.example.com"

    def test_web_settings_defaults_to_production(self, monkeypatch):
        """Test that WebSettings defaults to production when IS_DEV is not set"""
        monkeypatch.delenv("IS_DEV", raising=False)
        monkeypatch.setenv("BOTTOKEN", "prod_token_789")
        monkeypatch.setenv("WEBHOOK_URL", "http://prod.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.is_dev is False
        assert settings.bot_token == "prod_token_789"

    def test_web_settings_admin_credentials(self, monkeypatch):
        """Test admin credentials configuration"""
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_token")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")
        monkeypatch.setenv("ADMIN_KORAIL_ID", "admin_id")
        monkeypatch.setenv("ADMIN_KORAIL_PW", "admin_pw")

        from config import WebSettings

        settings = WebSettings()

        assert settings.admin_korail_id == "admin_id"
        assert settings.admin_korail_pw == "admin_pw"
        assert settings.admin_password == "admin_password"


class TestCelerySettings:
    """Test CelerySettings configuration"""

    def test_celery_settings_custom(self, monkeypatch):
        """Test CelerySettings with custom values"""
        monkeypatch.setenv("CELERY_BROKER", "redis://custom:6380")
        monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://custom:6380")

        from config import CelerySettings

        settings = CelerySettings()

        assert settings.celery_broker == "redis://custom:6380"
        assert settings.celery_result_backend == "redis://custom:6380"

    def test_celery_pool_defaults_to_threads(self, monkeypatch):
        monkeypatch.delenv("CELERY_POOL", raising=False)
        monkeypatch.delenv("CELERY_CONCURRENCY", raising=False)
        monkeypatch.setenv("MAX_CONCURRENT_RESERVATIONS", "25")
        from config import CelerySettings, WebSettings

        settings = CelerySettings(_env_file=None)

        assert settings.celery_pool == "threads"
        assert WebSettings(_env_file=None).celery_pool == "threads"
        # 동시 실행 수는 전체 동시 예약 한도를 따라감
        assert settings.worker_concurrency == 25

    def test_celery_pool_and_concurrency_from_env(self, monkeypatch):
        monkeypatch.setenv("CELERY_POOL", "prefork")
        monkeypatch.setenv("CELERY_CONCURRENCY", "4")
        from config import CelerySettings

        settings = CelerySettings(_env_file=None)

        assert settings.celery_pool == "prefork"
        assert settings.worker_concurrency == 4

    def test_celery_pool_rejects_unknown(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("CELERY_POOL", "gevent")
        from config import CelerySettings

        with pytest.raises(ValidationError):
            CelerySettings(_env_file=None)


class TestGetSettings:
    """Test get_settings function"""

    def test_get_settings_web_context(self, monkeypatch):
        """Test get_settings returns web_settings in web context"""
        monkeypatch.delenv("CELERY_WORKER_NAME", raising=False)
        monkeypatch.setenv("_", "/usr/bin/python")

        from config import get_settings, WebSettings

        settings = get_settings()
        assert isinstance(settings, WebSettings)

    def test_get_settings_celery_context(self, monkeypatch):
        """Celery 워커 안에서는 celery_settings (환경변수는 호출할 때 읽음)"""
        monkeypatch.setenv("CELERY_WORKER_NAME", "worker1")

        from config import CelerySettings, celery_settings, get_settings

        settings = get_settings()
        assert isinstance(settings, CelerySettings)
        assert settings is celery_settings


@pytest.mark.unit
def test_settings_errors_do_not_leak_env_values(monkeypatch):
    """설정 검증 오류에 환경변수 값(비밀번호 등)이 포함되지 않아야 함"""
    from pydantic import ValidationError
    from config import WebSettings

    monkeypatch.delenv("ADMINPW", raising=False)
    monkeypatch.setenv("ADMIN_KORAIL_PW", "super-secret-value")
    with pytest.raises(ValidationError) as exc:
        WebSettings(_env_file=None)
    assert "super-secret-value" not in str(exc.value)
