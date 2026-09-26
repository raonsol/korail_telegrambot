"""
Integration tests for Celery execution mode
"""

from unittest.mock import Mock, patch

import pytest

from core.crypto import CredentialVault
from core.db import Database
from core.launchers import CeleryLauncher, SubprocessLauncher, create_launcher
from core.schemas import Owner
from core.services import build_services


def _spec(**overrides):
    spec = {
        "reservation_id": "a" * 32,
        "callback_url": "http://localhost:8390/internal/events",
        "callback_token": "tok",
        "korail_id": "010-1234-5678",
        "korail_pw": "test_password",
        "dep_date": "20250115",
        "src_station": "서울",
        "dst_station": "부산",
        "dep_time": "0900",
        "max_dep_time": "1200",
        "train_type": "KTX",
        "seat_type": "general",
    }
    spec.update(overrides)
    return spec


@pytest.mark.integration
@pytest.mark.celery
class TestCeleryLauncher:
    def test_launch_uses_reservation_id_as_task_id_and_encrypts_password(self):
        vault = CredentialVault("secret")
        launcher = CeleryLauncher(Mock(), vault=vault)

        with patch("telegramBot.tasks.reservation_task.apply_async") as apply_async:
            ref = launcher.launch(_spec())

        assert ref == "a" * 32
        kwargs = apply_async.call_args.kwargs
        assert kwargs["task_id"] == "a" * 32
        payload = kwargs["kwargs"]["spec"]
        # 브로커에 평문 비밀번호가 실리지 않음
        assert "korail_pw" not in payload
        assert vault.decrypt(payload["korail_pw_enc"]) == "test_password"

    def test_launch_without_persistent_key_keeps_plaintext(self):
        launcher = CeleryLauncher(Mock(), vault=CredentialVault(""))

        with patch("telegramBot.tasks.reservation_task.apply_async") as apply_async:
            launcher.launch(_spec())

        assert apply_async.call_args.kwargs["kwargs"]["spec"]["korail_pw"] == (
            "test_password"
        )

    def test_cancel_revokes_task_and_cleans_redis(self, mock_redis_client):
        celery_app = Mock()
        mock_redis_client.hset("reservation_task:task-1", "status", "running")
        launcher = CeleryLauncher(celery_app, mock_redis_client)

        launcher.cancel("task-1")

        celery_app.control.revoke.assert_called_once_with("task-1", terminate=True)
        assert not mock_redis_client.exists("reservation_task:task-1")

    def test_create_launcher_uses_celery_when_redis_available(self, mock_redis_client):
        with patch("redis.Redis.from_url", return_value=mock_redis_client):
            launcher = create_launcher(True, "redis://localhost:6379")
        assert isinstance(launcher, CeleryLauncher)

    def test_create_launcher_falls_back_to_subprocess(self):
        broken = Mock()
        broken.ping = Mock(side_effect=Exception("Connection refused"))
        with patch("redis.Redis.from_url", return_value=broken):
            launcher = create_launcher(True, "redis://localhost:6379")
        assert isinstance(launcher, SubprocessLauncher)

    def test_create_launcher_subprocess_mode(self):
        assert isinstance(create_launcher(False, "redis://x"), SubprocessLauncher)

    @pytest.mark.asyncio
    async def test_service_with_celery_launcher(self, test_settings, valid_request):
        celery_app = Mock()
        launcher = CeleryLauncher(celery_app, vault=CredentialVault("secret"))
        services = build_services(
            test_settings, db=Database("sqlite://"), launcher=launcher
        )
        services.init_storage()
        owner = Owner(user_id="01012345678")

        with patch("telegramBot.tasks.reservation_task.apply_async"):
            first = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="telegram", chat_id=1
            )
            second = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="web"
            )

        # 같은 사용자의 여러 예약이 독립적으로 추적됨
        active = services.reservations.list(owner, active=True)
        assert {r.id for r in active} == {first.id, second.id}
        assert all(r.status.value == "queued" for r in active)

        await services.reservations.cancel(first.id, owner=owner, source="web")
        celery_app.control.revoke.assert_called_once_with(first.id, terminate=True)
        assert [r.id for r in services.reservations.list(owner, active=True)] == [
            second.id
        ]


@pytest.mark.integration
@pytest.mark.celery
class TestCeleryTasks:
    """Test Celery tasks"""

    def test_celery_app_configuration(self):
        """Test Celery app is configured correctly"""
        from telegramBot.tasks import app

        assert app.conf.broker_url
        assert app.conf.result_backend
        assert app.conf.task_serializer == "json"
        assert app.conf.accept_content == ["json"]

    def test_reservation_task_success(self, mock_redis_client):
        from telegramBot.tasks import reservation_task

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation") as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            run.side_effect = lambda spec, reporter, should_stop, on_success: (
                on_success() or {"status": "success"}
            )
            result = reservation_task.apply(
                kwargs={"spec": _spec()}, task_id="task-ok"
            ).get()

        assert result == {"status": "success"}
        assert run.call_args[0][0]["korail_pw"] == "test_password"
        assert mock_redis_client.hget("reservation_task:task-ok", "status") == (
            "completed"
        )

    def test_reservation_task_decrypts_password(self, mock_redis_client, monkeypatch):
        from config import web_settings
        from telegramBot.tasks import reservation_task

        monkeypatch.setattr(web_settings, "webapp_enc_key", "secret")
        encrypted = CredentialVault("secret").encrypt("pw-from-broker")
        spec = _spec(korail_pw_enc=encrypted)
        spec.pop("korail_pw")

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch(
            "telegramBot.tasks.run_reservation", return_value={"status": "failed"}
        ) as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": spec}, task_id="task-enc")

        assert run.call_args[0][0]["korail_pw"] == "pw-from-broker"

    def test_reservation_task_undecryptable_password(self, mock_redis_client):
        from telegramBot.tasks import reservation_task

        spec = _spec(korail_pw_enc="garbage")
        spec.pop("korail_pw")
        reporter = Mock()

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation") as run, patch(
            "telegramBot.tasks.build_reporter", return_value=reporter
        ):
            result = reservation_task.apply(
                kwargs={"spec": spec}, task_id="task-bad"
            ).get()

        run.assert_not_called()
        assert result["status"] == "error"
        assert reporter.send.call_args[0][0] == "error"

    def test_reservation_task_skips_completed(self, mock_redis_client):
        from telegramBot.tasks import reservation_task

        mock_redis_client.hset("reservation_task:task-done", "status", "completed")
        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation") as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            result = reservation_task.apply(
                kwargs={"spec": _spec()}, task_id="task-done"
            ).get()

        run.assert_not_called()
        assert result == {"status": "already_completed"}

    def test_reservation_task_without_redis(self):
        from telegramBot.tasks import reservation_task

        with patch(
            "telegramBot.tasks.redis.Redis.from_url",
            side_effect=Exception("no redis"),
        ), patch(
            "telegramBot.tasks.run_reservation", return_value={"status": "success"}
        ) as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            result = reservation_task.apply(
                kwargs={"spec": _spec()}, task_id="task-noredis"
            ).get()

        assert result == {"status": "success"}
        assert run.call_args.kwargs["should_stop"]() is False
