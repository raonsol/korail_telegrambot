"""
Integration tests for subprocess execution mode

실제 자식 프로세스를 띄워 Launcher ↔ ReservationService ↔ /internal/events 흐름을 검증합니다.
(코레일 API는 호출하지 않음)
"""

import asyncio
import io
import json
import sys
import time
from unittest.mock import Mock, patch

import httpx
import pytest

from core.db import Database
from core.launchers import SubprocessLauncher
from core.schemas import Owner
from core.services import build_services


def _services(test_settings, launcher):
    svc = build_services(test_settings, db=Database("sqlite://"), launcher=launcher)
    svc.init_storage()
    return svc


async def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.integration
@pytest.mark.subprocess
class TestSubprocessLauncher:
    def test_spec_is_sent_via_stdin_not_argv(self):
        """비밀번호가 프로세스 인자(ps)에 노출되지 않아야 함"""
        launcher = SubprocessLauncher()
        spec = {"reservation_id": "abcd1234", "korail_pw": "secret-pw"}

        with patch("core.launchers.subprocess.Popen") as mock_popen, patch(
            "builtins.open"
        ), patch("core.launchers.threading.Thread"):
            process = Mock(pid=4321, stdin=io.StringIO())
            process.stdin.close = Mock()
            mock_popen.return_value = process

            ref = launcher.launch(spec)

        assert ref == "4321"
        argv = mock_popen.call_args[0][0]
        assert "secret-pw" not in " ".join(argv)
        assert argv[-2:] == ["-m", "telegramBot.worker"]
        assert json.loads(process.stdin.getvalue()) == spec

    @pytest.mark.asyncio
    async def test_process_exit_without_report_marks_error(
        self, test_settings, valid_request
    ):
        # 명세만 읽고 결과 보고 없이 종료하는 워커
        launcher = SubprocessLauncher(
            command=[
                sys.executable,
                "-c",
                "import sys, json; json.load(sys.stdin); sys.exit(3)",
            ]
        )
        services = _services(test_settings, launcher)

        reservation = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )

        def is_error():
            r = services.reservations.get(reservation.id, Owner(user_id="01012345678"))
            return r.status.value == "error"

        assert await _wait_until(is_error)
        r = services.reservations.get(reservation.id, Owner(user_id="01012345678"))
        assert "code 3" in r.error

    @pytest.mark.asyncio
    async def test_cancel_terminates_process(self, test_settings, valid_request):
        launcher = SubprocessLauncher(
            command=[sys.executable, "-c", "import time; time.sleep(30)"]
        )
        services = _services(test_settings, launcher)
        exits = []
        original = launcher.on_exit
        launcher.on_exit = lambda rid, code: (exits.append(code), original(rid, code))

        owner = Owner(user_id="01012345678")
        reservation = await services.reservations.start(
            owner, valid_request, "010", "pw", origin="web"
        )
        cancelled = await services.reservations.cancel(
            reservation.id, owner=owner, source="web"
        )

        assert cancelled.status.value == "cancelled"
        assert await _wait_until(lambda: exits)
        assert exits[0] != 0  # SIGTERM
        # 취소 후 프로세스 종료가 상태를 덮어쓰지 않음
        await asyncio.sleep(0.1)
        r = services.reservations.get(reservation.id, owner)
        assert r.status.value == "cancelled"


@pytest.mark.integration
@pytest.mark.subprocess
class TestWorkerEntrypoint:
    def test_worker_reads_spec_from_stdin(self):
        from telegramBot import worker

        spec = {field: "x" for field in worker.REQUIRED_FIELDS}
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), patch.object(
            worker, "run_reservation", return_value={"status": "success"}
        ) as run, patch.object(worker, "build_reporter") as build_reporter, patch(
            "logging.FileHandler"
        ):
            assert worker.main() == 0

        run.assert_called_once()
        assert run.call_args[0][0] == spec
        build_reporter.assert_called_once_with(spec)

    def test_worker_rejects_incomplete_spec(self):
        from telegramBot import worker

        with patch("sys.stdin", io.StringIO('{"reservation_id": "x"}')), patch(
            "logging.FileHandler"
        ):
            assert worker.main() == 2

    def test_worker_reports_crash(self):
        from telegramBot import worker

        spec = {field: "x" for field in worker.REQUIRED_FIELDS}
        reporter = Mock()
        with patch("sys.stdin", io.StringIO(json.dumps(spec))), patch.object(
            worker, "run_reservation", side_effect=RuntimeError("boom")
        ), patch.object(worker, "build_reporter", return_value=reporter), patch(
            "logging.FileHandler"
        ):
            assert worker.main() == 1

        assert reporter.send.call_args[0][0] == "error"


@pytest.mark.integration
@pytest.mark.subprocess
class TestCallbackOverHttp:
    @pytest.mark.asyncio
    async def test_worker_callback_updates_reservation(
        self, services, fake_launcher, valid_request
    ):
        from web.factory import create_app

        app = create_app(services.settings, services, run_housekeeping=False)
        owner = Owner(user_id="01012345678")
        reservation = await services.reservations.start(
            owner, valid_request, "010", "pw", origin="web"
        )
        token = fake_launcher.launched[0]["callback_token"]

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            bad = await c.post(
                "/internal/events",
                json={
                    "reservation_id": reservation.id,
                    "token": "x",
                    "status": "success",
                },
            )
            assert bad.status_code == 403

            ok = await c.post(
                "/internal/events",
                json={
                    "reservation_id": reservation.id,
                    "token": token,
                    "status": "success",
                    "attempts": 12,
                    "train_info": "KTX 101",
                },
            )
            assert ok.status_code == 200
            assert ok.json() == {"applied": True}

            # 종료된 예약에 대한 늦은 보고는 무시
            late = await c.post(
                "/internal/events",
                json={
                    "reservation_id": reservation.id,
                    "token": token,
                    "status": "failed",
                },
            )
            assert late.json() == {"applied": False}

        r = services.reservations.get(reservation.id, owner)
        assert r.status.value == "success"
        assert r.attempts == 12
        assert r.result_text == "KTX 101"
