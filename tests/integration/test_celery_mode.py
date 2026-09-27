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

    def test_cancel_marks_cancelled_and_revokes_without_terminate(
        self, mock_redis_client
    ):
        """threads 풀(기본): 강제 종료 대신 Redis 취소 표시로 멈춤"""
        celery_app = Mock()
        mock_redis_client.hset("reservation_task:task-1", "status", "running")
        launcher = CeleryLauncher(celery_app, mock_redis_client)

        launcher.cancel("task-1")

        celery_app.control.revoke.assert_called_once_with("task-1", terminate=False)
        assert mock_redis_client.hget("reservation_task:task-1", "status") == (
            "cancelled"
        )
        assert mock_redis_client.ttl("reservation_task:task-1") > 0

    def test_cancel_terminates_on_prefork(self, mock_redis_client):
        celery_app = Mock()
        launcher = CeleryLauncher(celery_app, mock_redis_client, terminate=True)

        launcher.cancel("task-1")

        celery_app.control.revoke.assert_called_once_with("task-1", terminate=True)
        assert mock_redis_client.hget("reservation_task:task-1", "status") == (
            "cancelled"
        )

    def test_cancel_revokes_even_if_redis_fails(self):
        celery_app = Mock()
        broken = Mock()
        broken.pipeline = Mock(side_effect=Exception("Connection refused"))
        launcher = CeleryLauncher(celery_app, broken)

        launcher.cancel("task-1")

        celery_app.control.revoke.assert_called_once_with("task-1", terminate=False)

    def test_create_launcher_uses_celery_when_redis_available(self, mock_redis_client):
        with patch("redis.Redis.from_url", return_value=mock_redis_client):
            launcher = create_launcher(True, "redis://localhost:6379")
        assert isinstance(launcher, CeleryLauncher)
        assert launcher.terminate is False

    def test_create_launcher_prefork_terminates(self, mock_redis_client):
        with patch("redis.Redis.from_url", return_value=mock_redis_client):
            launcher = create_launcher(
                True, "redis://localhost:6379", celery_pool="prefork"
            )
        assert launcher.terminate is True

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
        celery_app.control.revoke.assert_called_once_with(first.id, terminate=False)
        assert [r.id for r in services.reservations.list(owner, active=True)] == [
            second.id
        ]
        services.db.dispose()


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

    def test_reservation_task_skips_cancelled(self, mock_redis_client):
        """대기열에 있던 중 취소된 예약은 시작하지 않음"""
        from telegramBot.tasks import reservation_task

        mock_redis_client.hset("reservation_task:task-c", "status", "cancelled")
        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation") as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            result = reservation_task.apply(
                kwargs={"spec": _spec()}, task_id="task-c"
            ).get()

        run.assert_not_called()
        assert result == {"status": "already_cancelled"}

    def test_reservation_task_stops_when_cancelled_while_running(
        self, mock_redis_client
    ):
        """실행 중 웹 서버가 취소 표시를 남기면 should_stop이 True가 됨"""
        from telegramBot.tasks import STATE_TTL_SECONDS, reservation_task

        seen = {}

        def fake_run(spec, reporter, should_stop, on_success):
            seen["before"] = should_stop()
            seen["ttl"] = mock_redis_client.ttl("reservation_task:task-r")
            CeleryLauncher(Mock(), mock_redis_client).cancel("task-r")
            seen["after"] = should_stop()
            return {"status": "stopped"}

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation", side_effect=fake_run), patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="task-r")

        assert seen["before"] is False
        assert 0 < seen["ttl"] <= STATE_TTL_SECONDS
        assert seen["after"] is True
        # 취소 뒤에는 완료로 덮어쓰지 않음
        assert mock_redis_client.hget("reservation_task:task-r", "status") == (
            "cancelled"
        )

    def test_reservation_task_does_not_overwrite_cancel(self, mock_redis_client):
        """시작 확인과 running 기록 사이에 취소돼도 cancelled가 유지됨 (HSETNX)"""
        from telegramBot.tasks import reservation_task

        real_hget = mock_redis_client.hget
        calls = {"n": 0}

        def racy_hget(key, field):
            calls["n"] += 1
            value = real_hget(key, field)
            if calls["n"] == 1:
                # 시작 확인 직후 웹 서버가 취소
                mock_redis_client.hset(key, "status", "cancelled")
            return value

        mock_redis_client.hget = racy_hget
        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch(
            "telegramBot.tasks.run_reservation",
            side_effect=lambda spec, reporter, should_stop, on_success: {
                "stopped": should_stop()
            },
        ), patch(
            "telegramBot.tasks.build_reporter"
        ):
            result = reservation_task.apply(
                kwargs={"spec": _spec()}, task_id="task-race"
            ).get()

        assert result == {"stopped": True}
        assert real_hget("reservation_task:task-race", "status") == "cancelled"

    def test_reservation_task_passes_max_duration(self, mock_redis_client):
        from telegramBot.tasks import RESERVATION_TIMEOUT, reservation_task

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch(
            "telegramBot.tasks.run_reservation", return_value={"status": "failed"}
        ) as run, patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="t1")
            reservation_task.apply(
                kwargs={"spec": _spec(max_duration=600)}, task_id="t2"
            )
            reservation_task.apply(
                kwargs={"spec": _spec(max_duration=RESERVATION_TIMEOUT * 10)},
                task_id="t3",
            )

        durations = [c.args[0]["max_duration"] for c in run.call_args_list]
        # 없으면 워커 설정, 있으면 워커 설정을 넘지 않는 범위에서 그대로
        assert durations == [RESERVATION_TIMEOUT, 600, RESERVATION_TIMEOUT]

    def test_redis_client_is_shared(self, mock_redis_client):
        from telegramBot.tasks import reservation_task

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ) as from_url, patch(
            "telegramBot.tasks.run_reservation", return_value={"status": "failed"}
        ), patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="s1")
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="s2")

        from_url.assert_called_once()

    def test_celery_worker_settings(self):
        from telegramBot.tasks import RESERVATION_TIMEOUT, app

        assert app.conf.worker_pool == "threads"
        assert app.conf.worker_concurrency >= 1
        # 실행 중인 태스크가 재전달(중복 실행)되지 않도록 최대 실행 시간보다 길게
        visibility = app.conf.broker_transport_options["visibility_timeout"]
        assert visibility > app.conf.task_time_limit > RESERVATION_TIMEOUT
        assert app.conf.task_soft_time_limit > RESERVATION_TIMEOUT

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


@pytest.mark.integration
@pytest.mark.celery
class TestWorkerLossDetection:
    """워커 재시작·크래시 감지 (Redis heartbeat + 종료 시 보고)"""

    def test_find_lost_requires_started_task_without_heartbeat(self, mock_redis_client):
        from core.task_state import heartbeat_key, state_key

        r = mock_redis_client
        r.hset(state_key("alive"), "status", "running")
        r.set(heartbeat_key("alive"), "1", ex=60)
        r.hset(state_key("dead"), "status", "running")  # heartbeat 만료
        r.hset(state_key("done"), "status", "completed")
        r.hset(state_key("gone"), "status", "cancelled")
        # "queued": 아직 대기열에 있어 상태 키가 없음

        launcher = CeleryLauncher(Mock(), r)
        refs = ["alive", "dead", "done", "gone", "queued"]

        # done: 성공했지만 보고가 전달되지 않았을 수 있음 → 웹 서버가 결과를 복구
        # (웹 서버는 DB에서 진행 중인 예약만 넘기므로 이미 보고된 예약은 대상이 아님)
        assert launcher.find_lost(refs) == ["dead", "done"]
        assert launcher.find_lost([]) == []
        assert CeleryLauncher(Mock()).find_lost(refs) == []  # Redis 없으면 판단 안 함

    def test_task_keeps_heartbeat_while_running_and_removes_it(self, mock_redis_client):
        from core.task_state import heartbeat_key
        from telegramBot.tasks import reservation_task

        seen = {}

        def fake_run(spec, reporter, should_stop, on_success):
            seen["ttl"] = mock_redis_client.ttl(heartbeat_key("task-hb"))
            seen["status"] = mock_redis_client.hget(
                "reservation_task:task-hb", "status"
            )
            return {"status": "failed"}

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation", side_effect=fake_run), patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="task-hb")

        assert 0 < seen["ttl"] <= 60
        assert seen["status"] == "running"
        # 끝나면 heartbeat 삭제 (결과 보고가 실패했으면 웹 서버가 바로 정리할 수 있게)
        assert not mock_redis_client.exists(heartbeat_key("task-hb"))
        # 끝난 태스크는 갱신 대상에서 빠짐
        from telegramBot.tasks import heartbeat

        assert "task-hb" not in heartbeat._ids

    def test_heartbeat_refreshes_running_reservations(self, mock_redis_client):
        from core.task_state import heartbeat_key
        from telegramBot.tasks import Heartbeat

        hb = Heartbeat(interval=3600)  # 스레드는 대기만 하고 beat()를 직접 호출
        hb.add(mock_redis_client, "r1")
        hb.add(mock_redis_client, "r2")
        hb.beat()
        assert mock_redis_client.ttl(heartbeat_key("r1")) > 0
        assert mock_redis_client.ttl(heartbeat_key("r2")) > 0

        hb.remove(mock_redis_client, "r1")
        mock_redis_client.delete(heartbeat_key("r2"))
        hb.beat()
        assert not mock_redis_client.exists(heartbeat_key("r1"))
        assert mock_redis_client.exists(heartbeat_key("r2"))

    def test_worker_shutdown_stops_and_reports_restart(self, mock_redis_client):
        """워커 종료가 시작되면 다음 시도 전에 멈추고 웹 서버에 바로 알림"""
        import telegramBot.tasks as tasks

        reporter = Mock()
        seen = {}

        def fake_run(spec, reporter, should_stop, on_success):
            seen["before"] = should_stop()
            tasks._on_worker_shutting_down(sig="TERM", how="Warm", exitcode=0)
            seen["after"] = should_stop()
            return {"status": "stopped", "attempts": 3}

        try:
            with patch(
                "telegramBot.tasks.redis.Redis.from_url",
                return_value=mock_redis_client,
            ), patch("telegramBot.tasks.run_reservation", side_effect=fake_run), patch(
                "telegramBot.tasks.build_reporter", return_value=reporter
            ):
                result = tasks.reservation_task.apply(
                    kwargs={"spec": _spec()}, task_id="task-shutdown"
                ).get()
        finally:
            tasks._shutting_down.clear()

        assert seen == {"before": False, "after": True}
        assert result == {"status": "interrupted", "attempts": 3}
        reporter.send.assert_called_once_with(
            "error", message=tasks.WORKER_RESTART_MESSAGE
        )

    def test_worker_shutdown_signal_is_connected(self):
        from celery.signals import worker_shutting_down

        import telegramBot.tasks as tasks

        try:
            worker_shutting_down.send(sender="test", sig="TERM", how="Warm", exitcode=0)
            assert tasks._shutting_down.is_set()
        finally:
            tasks._shutting_down.clear()

    @pytest.mark.asyncio
    async def test_service_marks_reservations_of_lost_worker(
        self, test_settings, valid_request, mock_redis_client
    ):
        from core.reservations import WORKER_LOST_MESSAGE
        from core.task_state import heartbeat_key, state_key

        celery_app = Mock()
        launcher = CeleryLauncher(
            celery_app, mock_redis_client, vault=CredentialVault("secret")
        )
        services = build_services(
            test_settings, db=Database("sqlite://"), launcher=launcher
        )
        services.init_storage()
        owner = Owner(user_id="01012345678")
        events = []

        async def deliver(event):
            events.append(event)

        services.notifier.add(
            type("Recorder", (), {"deliver": staticmethod(deliver)})()
        )

        with patch("telegramBot.tasks.reservation_task.apply_async"):
            lost = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="web"
            )
            alive = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="web"
            )
            queued = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="web"
            )
        mock_redis_client.hset(state_key(lost.id), "status", "running")
        mock_redis_client.hset(state_key(alive.id), "status", "running")
        mock_redis_client.set(heartbeat_key(alive.id), "1", ex=60)
        events.clear()

        assert await services.reservations.detect_lost_workers() == 1

        r = services.reservations.get(lost.id, owner)
        assert r.status.value == "error"
        assert r.error == WORKER_LOST_MESSAGE
        assert services.reservations.get(alive.id, owner).is_active
        assert services.reservations.get(queued.id, owner).is_active
        assert [e.reservation.id for e in events] == [lost.id]
        # 브로커가 다시 전달해도 시작하지 않도록 취소 표시
        assert mock_redis_client.hget(state_key(lost.id), "status") == "cancelled"
        # 이미 처리한 예약은 다시 세지 않음
        assert await services.reservations.detect_lost_workers() == 0
        services.db.dispose()


@pytest.mark.integration
@pytest.mark.celery
class TestUnreportedSuccessCelery:
    def test_task_stores_result_before_reporting(self, mock_redis_client):
        from telegramBot.tasks import reservation_task

        def fake_run(spec, reporter, should_stop, on_success):
            on_success(train_info="KTX 101", attempts=9)
            return {"status": "success", "reported": False}

        with patch(
            "telegramBot.tasks.redis.Redis.from_url", return_value=mock_redis_client
        ), patch("telegramBot.tasks.run_reservation", side_effect=fake_run), patch(
            "telegramBot.tasks.build_reporter"
        ):
            reservation_task.apply(kwargs={"spec": _spec()}, task_id="task-res")

        launcher = CeleryLauncher(Mock(), mock_redis_client)
        assert launcher.recover_success("task-res") == {
            "train_info": "KTX 101",
            "attempts": 9,
        }
        mock_redis_client.hset("reservation_task:other", "status", "running")
        assert launcher.recover_success("other") is None
        assert launcher.recover_success("missing") is None

    @pytest.mark.asyncio
    async def test_lost_worker_check_recovers_success(
        self, test_settings, valid_request, mock_redis_client
    ):
        """성공 후 보고가 전달되지 않은 태스크는 오류가 아니라 성공으로 처리"""
        from core.task_state import state_key

        launcher = CeleryLauncher(
            Mock(), mock_redis_client, vault=CredentialVault("secret")
        )
        services = build_services(
            test_settings, db=Database("sqlite://"), launcher=launcher
        )
        services.init_storage()
        owner = Owner(user_id="01012345678")
        with patch("telegramBot.tasks.reservation_task.apply_async"):
            r = await services.reservations.start(
                owner, valid_request, "010", "pw", origin="web"
            )
        # 태스크가 끝나 heartbeat는 없고, Redis에 성공 결과만 남음
        mock_redis_client.hset(
            state_key(r.id),
            mapping={"status": "completed", "train_info": "KTX 101", "attempts": 3},
        )

        assert await services.reservations.detect_lost_workers() == 1

        out = services.reservations.get(r.id, owner)
        assert out.status.value == "success"
        assert out.result_text == "KTX 101"
        # 이후에는 다시 실행되지 않도록 취소 표시
        assert mock_redis_client.hget(state_key(r.id), "status") == "cancelled"
        services.db.dispose()
