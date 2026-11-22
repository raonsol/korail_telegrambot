"""
Pytest configuration and fixtures for the Korail Telegram Bot test suite.
"""

import os
import pytest
from unittest.mock import Mock

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


@pytest.fixture(autouse=True)
def reset_shared_task_redis():
    """telegramBot.tasks.get_redis()가 캐시한 Redis 클라이언트를 테스트마다 초기화"""
    import sys

    tasks = sys.modules.get("telegramBot.tasks")
    if tasks is not None:
        tasks._redis_client = None
    yield
    tasks = sys.modules.get("telegramBot.tasks")
    if tasks is not None:
        tasks._redis_client = None


@pytest.fixture(autouse=True)
def reset_telegram_bot_class_state():
    """Reset class-level state shared by TelegramBot across tests."""
    try:
        from telegramBot.bot import TelegramBot

        TelegramBot.userDict = {}
        TelegramBot.subscribes = []
    except Exception:
        # Some tests may not import telegramBot.bot
        pass

    yield

    try:
        from telegramBot.bot import TelegramBot

        TelegramBot.userDict = {}
        TelegramBot.subscribes = []
    except Exception:
        pass


class FakeLauncher:
    """예약을 실제로 실행하지 않고 명세만 기록하는 Launcher"""

    name = "subprocess"

    def __init__(self):
        self.launched = []
        self.cancelled = []
        self.fail = False

    def launch(self, spec):
        if self.fail:
            raise RuntimeError("launch failed")
        self.launched.append(spec)
        return f"ref-{len(self.launched)}"

    def cancel(self, runner_ref):
        self.cancelled.append(runner_ref)


class KorailLoginStub:
    """코레일 로그인 대체: 비밀번호가 'correct'(또는 관리자 계정 비밀번호)면 성공"""

    def __init__(self, passwords=("correct", "admin_pass")):
        self.passwords = passwords
        self.calls = []
        self.devices = []

    def __call__(self, korail_id, password, device=None):
        self.calls.append((korail_id, password))
        self.devices.append(device)
        return password in self.passwords


@pytest.fixture
def test_settings():
    from config import web_settings

    return web_settings.model_copy(
        update={
            "enable_webapp": True,
            "webapp_enc_key": "test-encryption-key",
            "vapid_public_key": "",
            "vapid_private_key": "",
            "admin_password": "test_admin_password",
            "admin_korail_id": "admin_user",
            "admin_korail_pw": "admin_pass",
            "allow_list": "01012345678,01087654321",
            "max_concurrent_reservations": 10,
            "max_reservations_per_user": 3,
            "reservation_retention_days": 30,
            "internal_callback_url": "http://testserver/internal/events",
        }
    )


@pytest.fixture
def fake_launcher():
    return FakeLauncher()


@pytest.fixture
def korail_login():
    return KorailLoginStub()


@pytest.fixture
def services(test_settings, fake_launcher, korail_login):
    """메모리 SQLite + 가짜 Launcher로 구성한 서비스 (ALLOW_LIST 시드 완료)

    TEST_DATABASE_URL을 지정하면 해당 DB(예: PostgreSQL)에서 실행하고 테스트 후 테이블을 삭제합니다.
    """
    from core.db import Base, Database
    from core.services import build_services

    db = Database(os.getenv("TEST_DATABASE_URL", "sqlite://"))
    svc = build_services(
        test_settings,
        db=db,
        launcher=fake_launcher,
        korail_login=korail_login,
    )
    svc.init_storage()
    yield svc
    if os.getenv("TEST_DATABASE_URL"):
        Base.metadata.drop_all(db.engine)
    db.dispose()


@pytest.fixture
def valid_request():
    """항상 미래 날짜인 예약 요청"""
    from datetime import timedelta
    from core.schemas import ReservationRequest, now_kst

    return ReservationRequest(
        dep_date=now_kst().date() + timedelta(days=7),
        src_station="서울",
        dst_station="부산",
        dep_time="0900",
        max_dep_time="1200",
        train_type="KTX",
        seat_type="general",
    )


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
def mock_korail_client():
    """Mock Korail API client (pykorail)"""
    client = Mock()
    client.login = Mock(return_value=None)  # pykorail raises on failure
    client.trains.search = Mock(return_value=[])
    client.reservations.create = Mock(return_value=Mock())
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
    }


@pytest.fixture
def sample_train_data():
    """Sample train data for testing"""
    train = Mock()
    train.train_name = "KTX 001"
    train.dep_time = "090000"  # pykorail: HHMMSS
    train.arr_time = "113000"
    train.train_no = "001"
    return train


# Pytest hooks for custom test behavior
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
