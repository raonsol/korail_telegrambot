"""
Pytest configuration and fixtures for the Korail Telegram Bot test suite.
"""

import os
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from datetime import datetime

# Set environment variables for testing before importing modules
os.environ["IS_DEV"] = "true"
os.environ["BOTTOKEN_DEV"] = "test_bot_token_dev"
os.environ["BOTTOKEN"] = "test_bot_token_prod"
os.environ["WEBHOOK_URL_DEV"] = "http://test-dev.example.com"
os.environ["WEBHOOK_URL"] = "http://test-prod.example.com"
os.environ["ALLOW_LIST"] = "01012345678,01087654321"
os.environ["ADMINPW"] = "test_admin_password"
os.environ["ADMIN_KORAIL_ID"] = "admin_user"
os.environ["ADMIN_KORAIL_PW"] = "admin_pass"
os.environ["USE_CELERY"] = "false"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ["CELERY_BROKER"] = "redis://localhost:6379"
os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_telegram_bot():
    """Mock Telegram bot instance"""
    bot = Mock()
    bot.send_message = AsyncMock()
    bot.set_webhook = AsyncMock(return_value=True)
    bot.get_webhook_info = AsyncMock(
        return_value={"url": "http://test.example.com", "pending_update_count": 0}
    )
    return bot


@pytest.fixture
def mock_telegram_update():
    """Mock Telegram Update object"""
    update = Mock()
    update.message = Mock()
    update.message.chat_id = 123456789
    update.message.text = "test message"
    update.effective_chat = Mock()
    update.effective_chat.id = 123456789
    update.callback_query = None
    return update


@pytest.fixture
def mock_telegram_callback_query():
    """Mock Telegram CallbackQuery object"""
    query = Mock()
    query.answer = AsyncMock()
    query.message = Mock()
    query.message.chat_id = 123456789
    query.data = "test_callback"

    update = Mock()
    update.callback_query = query
    update.message = None
    update.effective_chat = Mock()
    update.effective_chat.id = 123456789
    return update


@pytest.fixture
def mock_korail_client():
    """Mock Korail API client"""
    client = Mock()
    client.login = Mock(return_value=True)
    client.search_train = Mock(return_value=[])
    client.reserve = Mock(return_value=Mock())
    client.username = "test_user"
    client.password = "test_password"
    return client


@pytest.fixture
def mock_redis_client():
    """Mock Redis client"""
    try:
        from fakeredis import FakeRedis

        return FakeRedis(decode_responses=True)
    except ImportError:
        # Fallback to mock if fakeredis not available
        redis_mock = Mock()
        redis_mock.ping = Mock(return_value=True)
        redis_mock.hset = Mock()
        redis_mock.hget = Mock(return_value=None)
        redis_mock.delete = Mock()
        redis_mock.from_url = Mock(return_value=redis_mock)
        return redis_mock


@pytest.fixture
def mock_celery_app():
    """Mock Celery application"""
    app = Mock()
    app.control = Mock()
    app.control.revoke = Mock()

    # Mock task
    task = Mock()
    task.delay = Mock(return_value=Mock(id="test-task-id"))
    app.task = Mock(return_value=task)

    return app


@pytest.fixture
def sample_user_data():
    """Sample user data for testing"""
    return {
        "inProgress": True,
        "lastAction": 4,
        "userInfo": {
            "korailId": "010-1234-5678",
            "korailPw": "test_password",
        },
        "trainInfo": {
            "depDate": "20250115",
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depTime": "0900",
            "maxDepTime": "1200",
            "trainType": "KTX",
            "trainTypeShow": "KTX",
            "specialInfo": "일반실 우선 예약",
            "specialInfoShow": "일반실 우선 예약",
        },
        "pid": 9999999,
    }


@pytest.fixture
def sample_train_data():
    """Sample train data for testing"""
    train = Mock()
    train.train_name = "KTX 001"
    train.dep_time = "09:00"
    train.arr_time = "11:30"
    train.train_no = "001"
    return train


@pytest.fixture
def sample_reservation_data():
    """Sample reservation data for Celery tasks"""
    return {
        "korail_id": "010-1234-5678",
        "korail_pw": "test_password",
        "dep_date": "20250115",
        "dep_station": "서울",
        "arr_station": "부산",
        "dep_time": "090000",
        "arr_time": "1200",
        "train_type": "KTX",
        "prefer_seat_type": "general",
        "attempts": 0,
    }


@pytest.fixture
def clean_environment():
    """Clean environment fixture that resets state between tests"""
    # Store original env vars
    original_env = os.environ.copy()

    yield

    # Restore original env vars
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for testing background processes"""
    process_mock = Mock()
    process_mock.pid = 12345
    process_mock.poll = Mock(return_value=None)  # Process is running
    process_mock.terminate = Mock()
    process_mock.kill = Mock()
    return process_mock


@pytest.fixture
def mock_requests():
    """Mock requests library"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"status": "ok"})
    mock_response.raise_for_status = Mock()

    mock_session = Mock()
    mock_session.post = Mock(return_value=mock_response)
    mock_session.get = Mock(return_value=mock_response)

    return mock_session


@pytest.fixture
def freezed_time(monkeypatch):
    """Fixture to freeze time for testing"""
    frozen_time = datetime(2025, 1, 15, 9, 0, 0)

    class FrozenDatetime:
        @classmethod
        def now(cls):
            return frozen_time

        @classmethod
        def today(cls):
            return frozen_time.date()

        @classmethod
        def strftime(cls, fmt):
            return frozen_time.strftime(fmt)

    monkeypatch.setattr("datetime.datetime", FrozenDatetime)
    return frozen_time


@pytest.fixture
def temp_log_dir(tmp_path):
    """Create a temporary log directory"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


# Pytest hooks for custom test behavior
def pytest_configure(config):
    """Configure pytest with custom settings"""
    config.addinivalue_line("markers", "unit: Unit tests for individual components")
    config.addinivalue_line(
        "markers", "integration: Integration tests for component interactions"
    )
    config.addinivalue_line("markers", "e2e: End-to-end tests for complete workflows")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically"""
    for item in items:
        # Add markers based on test location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)

        # Add slow marker to E2E tests
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.slow)
