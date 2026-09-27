"""
Integration tests for subprocess execution mode

실제 자식 프로세스를 띄워 Launcher ↔ ReservationService ↔ /internal/events 흐름을 검증합니다.
(코레일 API는 호출하지 않음)
"""

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

import httpx
import pytest

from core.db import Database
from core.launchers import LOGS_DIR, SubprocessLauncher
from core.schemas import Owner
from core.services import build_services

from . import fake_workers


@pytest.fixture
def make_services(test_settings):
    created = []

    def factory(launcher):
        svc = build_services(test_settings, db=Database("sqlite://"), launcher=launcher)
        svc.init_storage()
        created.append(svc)
        return svc

    yield factory
    for svc in created:
        svc.db.dispose()


async def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _process_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _log_path(reservation_id: str) -> str:
    return os.path.join(LOGS_DIR, f"worker_{reservation_id[:8]}.log")


@pytest.mark.integration
@pytest.mark.subprocess
class TestSubprocessLauncher:
    def test_spec_is_not_in_argv_and_modules_are_preloaded(self):
        """비밀번호가 프로세스 인자(ps)에 노출되지 않고, 워커 모듈은 forkserver에서 물려받음"""
        done = threading.Event()
        launcher = SubprocessLauncher(
            on_exit=lambda rid, code: done.set(),
            target=fake_workers.record_process_info,
        )
        spec = {"reservation_id": "argv0001", "korail_pw": "secret-pw"}
        if os.path.exists(_log_path("argv0001")):
            os.remove(_log_path("argv0001"))

        launcher.launch(spec)
        assert done.wait(20)

        with open(_log_path("argv0001")) as f:
            info = json.load(f)
        assert info["spec"] == spec
        assert "secret-pw" not in info["cmdline"]
        assert info["preloaded"] is True

    @pytest.mark.asyncio
    async def test_process_exit_without_report_marks_error(
        self, make_services, valid_request
    ):
        # 결과 보고 없이 종료하는 워커
        launcher = SubprocessLauncher(target=fake_workers.exit_with_code)
        services = make_services(launcher)

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
    async def test_cancel_terminates_process(self, make_services, valid_request):
        launcher = SubprocessLauncher(target=fake_workers.sleep_forever)
        services = make_services(launcher)
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

    @pytest.mark.asyncio
    async def test_unreported_success_is_recovered_from_result_file(
        self, make_services, valid_request
    ):
        """성공 보고가 전달되지 않고 워커가 끝나도 결과 파일로 성공 처리 (오류로 알리지 않음)"""
        from core.runner import result_file

        launcher = SubprocessLauncher(target=fake_workers.succeed_without_report)
        services = make_services(launcher)
        owner = Owner(user_id="01012345678")

        reservation = await services.reservations.start(
            owner, valid_request, "010", "pw", origin="web"
        )

        def finished():
            return not services.reservations.get(reservation.id, owner).is_active

        assert await _wait_until(finished)
        r = services.reservations.get(reservation.id, owner)
        assert r.status.value == "success"
        assert r.result_text == "KTX 101 서울→부산"
        assert r.attempts == 4
        # 복구한 결과 파일은 삭제
        assert not os.path.exists(result_file(LOGS_DIR, reservation.id))

    def test_cancel_only_terminates_that_worker(self):
        """워커는 웹 서버와 같은 프로세스 그룹이므로 그룹이 아닌 해당 프로세스만 종료"""
        launcher = SubprocessLauncher(target=fake_workers.sleep_forever)
        first = int(launcher.launch({"reservation_id": "keep0001"}))
        second = int(launcher.launch({"reservation_id": "stop0001"}))
        try:
            launcher.cancel(str(second))
            deadline = time.monotonic() + 10
            while _process_alive(second) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not _process_alive(second)
            assert _process_alive(first)
            assert launcher.running_count() == 1
        finally:
            launcher.cancel(str(first))

    def test_cancel_unknown_pid_does_nothing(self):
        """재시작 전 PID(다른 프로세스가 재사용했을 수 있음)는 종료하지 않음"""
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            SubprocessLauncher().cancel(str(sleeper.pid))
            time.sleep(0.2)
            assert sleeper.poll() is None
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_concurrent_launches_report_their_own_exit_codes(self):
        """시작과 종료 코드 읽기가 겹쳐도 코드가 뒤섞이거나 255가 되지 않음"""
        results = {}
        lock = threading.Lock()
        done = threading.Event()
        count = 24

        def on_exit(rid, code):
            with lock:
                results[rid] = code
                if len(results) == count:
                    done.set()

        launcher = SubprocessLauncher(
            on_exit=on_exit, target=fake_workers.exit_with_code
        )
        threads = [
            threading.Thread(
                target=launcher.launch,
                args=({"reservation_id": f"code{i:04d}", "code": 10 + i},),
            )
            for i in range(count)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert done.wait(30)
        assert results == {f"code{i:04d}": 10 + i for i in range(count)}
        assert launcher.running_count() == 0

    def test_real_entrypoint_rejects_incomplete_spec(self):
        """기본 진입점(telegramBot.worker.run_process): 로그 파일로 출력, 잘못된 명세는 코드 2"""
        codes = []
        done = threading.Event()
        launcher = SubprocessLauncher(
            on_exit=lambda rid, code: (codes.append(code), done.set())
        )
        launcher.launch({"reservation_id": "badspec1"})
        assert done.wait(20)
        assert codes == [2]
        with open(_log_path("badspec1")) as f:
            assert "Missing fields" in f.read()


# 웹 서버 역할: 워커를 띄운 뒤 SIGTERM을 받으면 정상 종료 (uvicorn과 같은 종료 경로)
SERVER_SCRIPT = """
import signal, sys, time
sys.path[:0] = {paths!r}
from core.launchers import SubprocessLauncher
from tests.integration import fake_workers

launcher = SubprocessLauncher(target=fake_workers.sleep_forever)
print(launcher.launch({{"reservation_id": "daemon01"}}), flush=True)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
while True:
    time.sleep(1)
"""


@pytest.mark.integration
@pytest.mark.subprocess
class TestServerShutdown:
    def test_workers_stop_with_server(self):
        """워커는 daemon: 웹 서버가 종료되면 기다리지 않고 함께 종료"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        paths = [os.path.join(root, "src"), root]
        server = subprocess.Popen(
            [sys.executable, "-c", SERVER_SCRIPT.format(paths=paths)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            worker_pid = int(server.stdout.readline())
            assert _process_alive(worker_pid)

            server.terminate()
            assert server.wait(timeout=10) == 0  # 워커가 끝날 때까지 기다리지 않음

            deadline = time.monotonic() + 10
            while _process_alive(worker_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not _process_alive(worker_pid)
        finally:
            server.kill()
            server.wait()


@pytest.mark.integration
@pytest.mark.subprocess
class TestOrphanWorker:
    def test_worker_stops_when_server_forgets_reservation(self):
        """웹 서버 DB가 초기화돼 예약을 모르면(404) 워커가 스스로 종료"""
        from http.server import BaseHTTPRequestHandler, HTTPServer

        calls = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(json.loads(body)["status"])
                # 처음 두 번(running, progress)만 받아들이고 이후엔 모르는 예약
                code = 200 if len(calls) <= 2 else 404
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"applied": true}' if code == 200 else b"{}")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        exits = []
        done = threading.Event()
        launcher = SubprocessLauncher(
            on_exit=lambda rid, code: (exits.append(code), done.set()),
            target=fake_workers.run_with_no_trains,
        )
        try:
            launcher.launch(
                {
                    "reservation_id": "orphan00",
                    "callback_url": f"http://127.0.0.1:{server.server_port}/internal/events",
                    "callback_token": "tok",
                    "korail_id": "010",
                    "korail_pw": "pw",
                    "dep_date": "20990101",
                    "src_station": "서울",
                    "dst_station": "부산",
                    "dep_time": "0900",
                    "max_dep_time": "1200",
                    "train_type": "KTX",
                    "seat_type": "general",
                }
            )
            assert done.wait(20), "worker did not stop after rejection"
        finally:
            server.shutdown()
            server.server_close()

        assert exits == [0]  # run_reservation이 "rejected"로 종료
        assert calls == ["running", "progress", "progress"]


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

    @pytest.mark.parametrize("reported, kept", [(True, False), (False, True)])
    def test_result_file_kept_until_success_is_delivered(
        self, tmp_path, reported, kept
    ):
        from telegramBot import worker

        spec = {field: "x" for field in worker.REQUIRED_FIELDS}
        path = tmp_path / "result_x.json"

        def fake_run(spec, reporter, on_success):
            on_success(train_info="KTX 101", attempts=2)
            assert json.loads(path.read_text()) == {
                "train_info": "KTX 101",
                "attempts": 2,
            }
            return {"status": "success", "reported": reported}

        with patch.object(
            worker, "run_reservation", side_effect=fake_run
        ), patch.object(worker, "build_reporter"):
            assert worker._run(spec, str(path)) == 0

        assert path.exists() is kept

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
