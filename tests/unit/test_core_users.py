"""사용자 DB 관리"""

import pytest

from core.errors import Conflict, NotFound, ValidationFailed


class TestUserService:
    def test_seeded_from_allow_list(self, services):
        ids = [u.id for u in services.users.list()]
        assert ids == ["01012345678", "01087654321"]

    def test_seed_is_idempotent_and_keeps_existing(self, services):
        services.users.update("01012345678", is_active=False, name="홍길동")

        added = services.users.seed_from_allow_list(
            "010-1234-5678, 01011112222, invalid"
        )

        assert added == 1
        user = services.users.get("01012345678")
        assert user.is_active is False  # 관리자 변경 사항 유지
        assert user.name == "홍길동"
        assert services.users.is_allowed("01011112222")

    def test_create_user(self, services):
        user = services.users.create("010-5555-6666", " 김철수 ")
        assert user.id == "01055556666"
        assert user.name == "김철수"
        assert services.users.is_allowed("01055556666")

    def test_create_duplicate(self, services):
        with pytest.raises(Conflict):
            services.users.create("01012345678")

    def test_create_invalid_phone(self, services):
        with pytest.raises(ValidationFailed):
            services.users.create("12345")

    def test_deactivate_and_delete(self, services):
        services.users.update("01012345678", is_active=False)
        assert not services.users.is_allowed("01012345678")

        services.users.delete("01012345678")
        assert services.users.get("01012345678") is None
        with pytest.raises(NotFound):
            services.users.delete("01012345678")

    def test_update_missing(self, services):
        with pytest.raises(NotFound):
            services.users.update("01099999999", name="x")

    def test_telegram_link(self, services):
        services.users.link_telegram("01012345678", 777)

        assert services.users.owner_for_chat(777) == "01012345678"
        assert services.users.telegram_target("01012345678") == 777
        assert services.users.get("01012345678").last_login_at is not None

        services.users.update("01012345678", telegram_notify=False)
        assert services.users.telegram_target("01012345678") is None

        services.users.unlink_telegram("01012345678")
        assert services.users.owner_for_chat(777) is None
