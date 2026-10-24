"""관리자 운영 설정 (저장·검증·서비스 반영·워커 전달)"""

from unittest.mock import Mock

import pytest

from core.errors import LimitExceeded, ValidationFailed
from core.models import AppSetting
from core.runner import runner_options, run_reservation


def _values(services):
    return {s.key: s.value for s in services.app_settings.describe()}


class TestAppSettingsService:
    def test_defaults_come_from_env(self, services):
        values = _values(services)
        settings = services.settings
        assert (
            values["max_concurrent_reservations"]
            == settings.max_concurrent_reservations
        )
        assert values["korail_egress_rpm"] == settings.korail_egress_rpm
        assert values["same_error_limit"] == 10
        assert values["attempt_interval"] == 2.0
        assert not any(s.overridden for s in services.app_settings.describe())

    def test_update_applies_and_persists(self, services):
        services.app_settings.update(
            {
                "attempt_interval": 3.5,
                "same_error_limit": 5,
                "korail_egress_rpm": 30,
                "korail_egress_max_active": 4,
                "max_reservations_per_user": 1,
            }
        )
        r = services.reservations
        assert r.max_active_per_user == 1
        assert r.runner_options["interval"] == 3.5
        assert r.runner_options["same_error_limit"] == 5
        assert services.egresses.rpm == 30
        assert services.egresses.max_active == 4
        # 이미 만든 출구 게이트(열차 목록 조회용)에도 바로 반영
        gate = services.egresses.gate(services.egresses.ids[0])
        services.app_settings.update({"korail_egress_rpm": 20})
        assert gate.rpm == 20

        with services.db.session() as s:
            assert s.get(AppSetting, "same_error_limit").value == "5"

        # 웹 서버가 다시 시작해도 DB 값으로 복원
        services.reservations.runner_options = {}
        services.app_settings._overrides = {}
        services.app_settings.load()
        assert services.reservations.runner_options["same_error_limit"] == 5
        described = {s.key: s for s in services.app_settings.describe()}
        assert described["same_error_limit"].overridden is True
        assert described["max_attempts"].overridden is False

    def test_reset_to_default(self, services):
        services.app_settings.update({"same_error_limit": 5})
        services.app_settings.update({"same_error_limit": None})
        assert _values(services)["same_error_limit"] == 10
        assert services.reservations.runner_options["same_error_limit"] == 10
        with services.db.session() as s:
            assert s.get(AppSetting, "same_error_limit") is None

    @pytest.mark.parametrize(
        "changes",
        [
            {"unknown": 1},
            {"same_error_limit": 0},
            {"same_error_limit": 2.5},
            {"attempt_interval": 0.7},  # 0.5 단위
            {"attempt_interval": 0.1},
            {"korail_egress_rpm": True},
            {"max_duration_minutes": 10_000},  # RESERVATION_TIMEOUT 초과
        ],
    )
    def test_rejects_invalid(self, services, changes):
        with pytest.raises(ValidationFailed):
            services.app_settings.update({"max_attempts": 500, **changes})
        # 하나라도 잘못되면 아무것도 바뀌지 않음
        assert _values(services)["max_attempts"] == 1000

    def test_ignores_invalid_stored_value(self, services):
        with services.db.session() as s:
            s.add(AppSetting(key="same_error_limit", value="-3", updated_at=None))
            s.add(AppSetting(key="removed_setting", value="1", updated_at=None))
        services.app_settings.load()
        assert _values(services)["same_error_limit"] == 10

    @pytest.mark.asyncio
    async def test_limits_apply_to_new_reservations(self, services, valid_request):
        from core.schemas import Owner

        services.app_settings.update({"max_reservations_per_user": 1})
        owner = Owner(user_id="01012345678", is_admin=False)
        await services.reservations.start(
            owner, valid_request, "010-1234-5678", "pw", origin="web"
        )
        with pytest.raises(LimitExceeded):
            await services.reservations.start(
                owner, valid_request, "010-1234-5678", "pw", origin="web"
            )

    @pytest.mark.asyncio
    async def test_worker_settings_travel_in_spec(
        self, services, fake_launcher, valid_request
    ):
        from core.schemas import Owner

        services.app_settings.update(
            {
                "attempt_interval": 4,
                "max_attempts": 50,
                "max_duration_minutes": 5,
                "korail_egress_rpm": 12,
            }
        )
        owner = Owner(user_id="01012345678", is_admin=False)
        await services.reservations.start(
            owner, valid_request, "010-1234-5678", "pw", origin="web"
        )
        spec = fake_launcher.launched[-1]
        assert spec["runner"]["interval"] == 4.0
        assert spec["runner"]["max_attempts"] == 50
        assert spec["max_duration"] == 300
        assert spec["egress_rpm"] == 12


class TestRunnerOptions:
    def test_reads_valid_options_only(self):
        spec = {
            "runner": {
                "interval": 3,
                "max_attempts": 7,
                "same_error_limit": "x",
                "progress_every": 0,
                "relogin_after_errors": True,
                "unknown": 1,
            }
        }
        assert runner_options(spec) == {"interval": 3.0, "max_attempts": 7}
        assert runner_options({}) == {}
        assert runner_options({"runner": "bad"}) == {}

    def test_worker_loop_uses_options(self):
        miss = {"success": False, "result": None, "error": "No trains available"}
        handler = Mock()
        handler.login = Mock(return_value=True)
        handler.reserve_single_attempt = Mock(return_value=miss)
        reporter = Mock()
        slept = []
        spec = {
            "reservation_id": "r1",
            "korail_id": "010-1234-5678",
            "korail_pw": "pw",
            "dep_date": "20250115",
            "src_station": "서울",
            "dst_station": "부산",
            "dep_time": "0900",
            "max_dep_time": "1200",
            "train_type": "KTX",
            "seat_type": "general",
            "runner": {"interval": 3.5, "max_attempts": 4},
        }
        result = run_reservation(
            spec,
            reporter,
            handler_factory=lambda: handler,
            sleep=slept.append,
            **runner_options(spec),
        )
        assert result == {"status": "failed", "attempts": 4}
        assert set(slept) == {3.5}
