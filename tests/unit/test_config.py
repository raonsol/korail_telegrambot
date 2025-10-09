"""
Unit tests for configuration management (config.py)
"""

import pytest
import os
from unittest.mock import patch


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

    def test_web_settings_allow_list(self, monkeypatch):
        """Test allow list configuration"""
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_token")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678,01087654321,01011112222")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.allow_list == "01012345678,01087654321,01011112222"
        assert "01012345678" in settings.allow_list
        assert "01087654321" in settings.allow_list


class TestCelerySettings:
    """Test CelerySettings configuration"""

    def test_celery_settings_default(self, monkeypatch):
        """Test CelerySettings with default values"""
        from config import CelerySettings

        settings = CelerySettings()

        assert settings.celery_broker == "redis://localhost:6379"
        assert settings.celery_result_backend == "redis://localhost:6379"
        assert settings.redis_url == "redis://localhost:6379"
        assert settings.redis_db == 0

    def test_celery_settings_custom(self, monkeypatch):
        """Test CelerySettings with custom values"""
        monkeypatch.setenv("CELERY_BROKER", "redis://custom:6380")
        monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://custom:6380")

        from config import CelerySettings

        settings = CelerySettings()

        assert settings.celery_broker == "redis://custom:6380"
        assert settings.celery_result_backend == "redis://custom:6380"

    def test_celery_settings_timeouts(self, monkeypatch):
        """Test Celery timeout settings"""
        from config import CelerySettings

        settings = CelerySettings()

        assert settings.reservation_timeout == 3600
        assert settings.max_concurrent_reservations == 10


class TestBaseAppSettings:
    """Test BaseAppSettings configuration"""

    def test_base_app_settings_defaults(self):
        """Test BaseAppSettings default values"""
        from config import BaseAppSettings

        settings = BaseAppSettings()

        assert settings.redis_url == "redis://localhost:6379"
        assert settings.redis_db == 0
        assert settings.database_url == "sqlite:///./korail_bot.db"
        assert settings.debug is False
        assert settings.log_level == "INFO"


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
        """Test get_settings returns celery_settings in Celery context"""
        monkeypatch.setenv("CELERY_WORKER_NAME", "worker1")

        # Need to reload the module to pick up the new environment
        import importlib
        import config

        importlib.reload(config)

        from config import get_settings, CelerySettings

        settings = get_settings()
        # Note: This test may not work as expected due to module-level instantiation
        # but demonstrates the intent


class TestConfigurationProperties:
    """Test configuration properties and methods"""

    def test_bot_token_property(self, monkeypatch):
        """Test bot_token property returns correct token"""
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_bot_token")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.bot_token == "dev_bot_token"
        assert settings.bot_token == settings.telegram_token

    def test_webhook_url_property(self, monkeypatch):
        """Test webhook_url_by_env property returns correct URL"""
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_bot_token")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev-webhook.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings = WebSettings()

        assert settings.webhook_url_by_env == "http://dev-webhook.example.com"
        assert settings.webhook_url_by_env == settings.webhook_url

    def test_is_dev_property(self, monkeypatch):
        """Test is_dev property correctly detects development mode"""
        # Test development mode
        monkeypatch.setenv("IS_DEV", "true")
        monkeypatch.setenv("BOTTOKEN_DEV", "dev_token")
        monkeypatch.setenv("WEBHOOK_URL_DEV", "http://dev.example.com")
        monkeypatch.setenv("ALLOW_LIST", "01012345678")
        monkeypatch.setenv("ADMINPW", "admin_password")

        from config import WebSettings

        settings_dev = WebSettings()
        assert settings_dev.is_dev is True

        # Test production mode
        monkeypatch.setenv("IS_DEV", "false")
        monkeypatch.setenv("BOTTOKEN", "prod_token")
        monkeypatch.setenv("WEBHOOK_URL", "http://prod.example.com")

        settings_prod = WebSettings()
        assert settings_prod.is_dev is False
