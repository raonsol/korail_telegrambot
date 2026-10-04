"""코레일 기기 신원(Android ID) 발급·전달과 DB 마이그레이션"""

import re
from unittest.mock import Mock, patch

import pytest
from pykorail.device import PROFILES_BY_ID
from sqlalchemy import create_engine, inspect, text

from core.db import Database
from core.models import Reservation, User
from core.runner import run_reservation
from core.schemas import Owner
from core.users import ADMIN_ACCOUNT_NAME

ANDROID_ID = re.compile(r"[0-9a-f]{16}")


def _stored(services, user_id):
    with services.db.session() as s:
        user = s.get(User, user_id)
        return user.korail_device_profile, user.korail_android_id


class TestUserDevice:
    def test_registered_user_gets_persistent_device(self, services):
        device = services.users.korail_device("010-1234-5678")

        assert ANDROID_ID.fullmatch(device["android_id"])
        assert device["profile_id"] in PROFILES_BY_ID
        assert _stored(services, "01012345678") == (
            device["profile_id"],
            device["android_id"],
        )
        # 하이픈 유무와 관계없이 같은 계정 = 같은 기기
        assert services.users.korail_device("01012345678") == device

    def test_each_user_gets_a_different_android_id(self, services):
        a = services.users.korail_device("01012345678")
        b = services.users.korail_device("01087654321")
        assert a["android_id"] != b["android_id"]

    def test_existing_device_is_not_replaced(self, services):
        with services.db.session() as s:
            user = s.get(User, "01012345678")
            user.korail_device_profile = "unknown-model"
            user.korail_android_id = "0123456789abcdef"

        assert services.users.korail_device("01012345678") == {
            "profile_id": "unknown-model",
            "android_id": "0123456789abcdef",
        }

    def test_unknown_account_has_no_stored_device(self, services):
        assert services.users.korail_device("01099999999") is None
        assert services.users.get("01099999999") is None  # 행을 만들지 않음


class TestAdminAccount:
    def test_admin_account_row_is_added_active(self, services):
        """ADMIN_KORAIL_ID(admin_user)는 시작 시 활성 사용자 행으로 추가됨"""
        user = services.users.get("admin_user")
        assert user is not None
        assert user.name == ADMIN_ACCOUNT_NAME
        assert user.is_active
        assert not services.users.ensure_admin_account()  # 이미 있으면 그대로

    def test_admin_phone_can_log_in_as_user(self, services):
        from core.users import UserService

        users = UserService(services.db, admin_korail_id="010-3333-4444")
        assert users.ensure_admin_account()
        assert users.is_allowed("01033334444")

    def test_existing_user_row_is_kept(self, services):
        from core.users import UserService

        users = UserService(services.db, admin_korail_id="010-1234-5678")
        assert not users.ensure_admin_account()
        assert services.users.get("01012345678").is_active

    def test_long_email_account_fits(self, services):
        from core.users import UserService

        email = "Long.Admin.Account.Name@example.com"  # 20자 초과
        users = UserService(services.db, admin_korail_id=email)
        assert users.ensure_admin_account()
        device = users.korail_device(email)
        assert _stored(services, email.lower()) == (
            device["profile_id"],
            device["android_id"],
        )

    def test_admin_device_is_stored_and_reused(self, services):
        device = services.users.korail_device("admin_user")

        assert ANDROID_ID.fullmatch(device["android_id"])
        assert _stored(services, "admin_user") == (
            device["profile_id"],
            device["android_id"],
        )
        assert services.users.korail_device("admin_user") == device

    def test_deleted_admin_row_is_recreated(self, services):
        services.users.delete("admin_user")

        device = services.users.korail_device("admin_user")

        assert device is not None
        assert services.users.get("admin_user").name == ADMIN_ACCOUNT_NAME


class TestDevicePropagation:
    @pytest.mark.asyncio
    async def test_web_login_uses_user_device(self, services, korail_login):
        await services.auth.login("01012345678", "correct")

        assert korail_login.devices == [services.users.korail_device("01012345678")]

    @pytest.mark.asyncio
    async def test_admin_login_uses_admin_row_device(self, services, korail_login):
        await services.auth.admin_login("test_admin_password")

        assert korail_login.devices == [services.users.korail_device("admin_user")]

    @pytest.mark.asyncio
    async def test_reservation_spec_carries_device(
        self, services, fake_launcher, valid_request
    ):
        await services.reservations.start(
            Owner(user_id="01012345678"),
            valid_request,
            "010-1234-5678",
            "pw",
            origin="web",
        )

        spec = fake_launcher.launched[-1]
        assert spec["korail_device"] == services.users.korail_device("01012345678")

    def test_runner_creates_handler_with_spec_device_and_egress(self):
        device = {"profile_id": "x", "android_id": "0123456789abcdef"}
        handler = Mock()
        handler.login = Mock(return_value=True)
        handler.reserve_single_attempt = Mock(
            return_value={"success": True, "result": "train", "error": None}
        )

        spec = {
            "reservation_id": "r1",
            "korail_id": "010-1234-5678",
            "korail_pw": "pw",
            "dep_date": "20250115",
            "src_station": "서울",
            "dst_station": "부산",
            "dep_time": "0900",
            "max_dep_time": "1200",
            "train_type": "ALL",
            "seat_type": "general",
            "korail_device": device,
            "egress_id": "home1",
            "egress_proxy": "socks5h://h:1080",
        }
        with patch("core.runner.ReserveHandler", return_value=handler) as cls:
            run_reservation(spec, Mock(), sleep=lambda _: None)

        cls.assert_called_once_with(
            proxy_url="socks5h://h:1080", egress_id="home1", device=device
        )


class TestKorailClientDevice:
    def test_client_uses_given_profile_and_android_id(self):
        from telegramBot.korail_client import create_korail_client

        profile_id = next(iter(PROFILES_BY_ID))
        device = {"profile_id": profile_id, "android_id": "0123456789abcdef"}
        client = create_korail_client(device=device)
        try:
            assert client.android_id == "0123456789abcdef"
            assert client.device_profile.id == profile_id
        finally:
            client.close()

    def test_unknown_profile_keeps_android_id(self):
        from telegramBot.korail_client import create_korail_client

        device = {"profile_id": "removed-model", "android_id": "0123456789abcdef"}
        client = create_korail_client(device=device)
        try:
            assert client.android_id == "0123456789abcdef"
            assert client.device_profile is None
        finally:
            client.close()

    def test_relogin_keeps_the_same_device(self):
        from telegramBot.korail_client import ReserveHandler

        device = {"profile_id": "x", "android_id": "0123456789abcdef"}
        handler = ReserveHandler(device=device)
        with patch(
            "telegramBot.korail_client.create_korail_client", return_value=Mock()
        ) as create:
            assert handler.login("010-1234-5678", "pw")
            assert handler.login("010-1234-5678", "pw")

        assert [c.kwargs["device"] for c in create.call_args_list] == [device, device]


LEGACY_DDL = [
    """
CREATE TABLE users (
    id VARCHAR(20) NOT NULL PRIMARY KEY,
    name VARCHAR(50),
    is_active BOOLEAN NOT NULL,
    telegram_chat_id BIGINT,
    telegram_notify BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    last_login_at DATETIME
)
""",
    """
CREATE TABLE reservations (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(20) NOT NULL,
    origin VARCHAR(10) NOT NULL,
    chat_id BIGINT,
    korail_id VARCHAR(50) NOT NULL,
    dep_date VARCHAR(8) NOT NULL,
    src_station VARCHAR(30) NOT NULL,
    dst_station VARCHAR(30) NOT NULL,
    dep_time VARCHAR(4) NOT NULL,
    max_dep_time VARCHAR(4) NOT NULL,
    train_type VARCHAR(10) NOT NULL,
    seat_type VARCHAR(20) NOT NULL,
    status VARCHAR(12) NOT NULL,
    runner VARCHAR(12) NOT NULL,
    runner_ref VARCHAR(64),
    callback_token_hash VARCHAR(64) NOT NULL,
    attempts INTEGER NOT NULL,
    result_text TEXT,
    error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    finished_at DATETIME
)
""",
]


def _head_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from core.db import MIGRATIONS_DIR

    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR)
    return ScriptDirectory.from_config(config).get_current_head()


class TestMigration:
    def test_fresh_database_is_created_at_head(self, tmp_path):
        db = Database(f"sqlite:///{tmp_path}/fresh.db")
        try:
            db.migrate()
            db.migrate()  # 두 번 실행해도 안전
            with db.engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                columns = {c["name"] for c in inspect(conn).get_columns("users")}
            assert version == _head_revision()
            assert {"korail_device_profile", "korail_android_id"} <= columns
        finally:
            db.dispose()

    def test_legacy_database_gets_device_columns(self, tmp_path):
        """Alembic 도입 전(create_all) DB: 데이터는 유지하고 컬럼만 추가"""
        url = f"sqlite:///{tmp_path}/legacy.db"
        engine = create_engine(url)
        with engine.begin() as conn:
            for ddl in LEGACY_DDL:
                conn.execute(text(ddl))
            conn.execute(
                text(
                    "INSERT INTO users (id, is_active, telegram_notify, created_at) "
                    "VALUES ('01012345678', 1, 1, '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO reservations VALUES ('r1', '01012345678', 'web', "
                    "NULL, '010', '20260101', '서울', '부산', '0900', '1200', 'KTX', "
                    "'general', 'success', 'subprocess', NULL, 'h', 3, 'KTX 101', "
                    "NULL, '2026-01-01', '2026-01-01', '2026-01-01')"
                )
            )
        engine.dispose()

        db = Database(url)
        try:
            db.migrate()
            with db.engine.connect() as conn:
                version = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                indexes = {
                    i["name"]: i["unique"] for i in inspect(conn).get_indexes("users")
                }
                id_type = next(
                    c["type"]
                    for c in inspect(conn).get_columns("users")
                    if c["name"] == "id"
                )
            assert version == _head_revision()
            assert indexes["ix_users_korail_android_id"]
            assert id_type.length == 50
            with db.session() as s:
                user = s.get(User, "01012345678")
                assert user.is_active
                assert user.korail_android_id is None
                assert s.get(Reservation, "r1").waitlisted is False
        finally:
            db.dispose()

    def test_android_id_is_unique(self, services):
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with services.db.session() as s:
                for user_id in ("01012345678", "01087654321"):
                    s.get(User, user_id).korail_android_id = "0123456789abcdef"
