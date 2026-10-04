"""워커 공통 예약 루프와 콜백 보고"""

from unittest.mock import Mock

import pytest
import requests

from core.egress import BLOCK_BACKOFF_SECONDS, MemoryGate
from core.runner import MAX_DURATION_MESSAGE, CallbackReporter, run_reservation

SPEC = {
    "reservation_id": "r1",
    "callback_url": "http://cb",
    "callback_token": "tok",
    "korail_id": "010-1234-5678",
    "korail_pw": "pw",
    "dep_date": "20250115",
    "src_station": "서울",
    "dst_station": "부산",
    "dep_time": "0900",
    "max_dep_time": "1200",
    "train_type": "ALL",
    "seat_type": "special_only",
}

MISS = {"success": False, "result": None, "error": "No trains available"}


def _handler(results, login=True):
    handler = Mock()
    handler.login = (
        Mock(return_value=login)
        if not isinstance(login, list)
        else Mock(side_effect=login)
    )
    handler.reserve_single_attempt = Mock(side_effect=results)
    return handler


def _run(handler, **kwargs):
    reporter = Mock()
    kwargs.setdefault("sleep", lambda _: None)
    result = run_reservation(SPEC, reporter, handler_factory=lambda: handler, **kwargs)
    statuses = [c.args[0] for c in reporter.send.call_args_list]
    return result, reporter, statuses


class TestRunReservation:
    def test_success_after_misses(self):
        handler = _handler(
            [MISS, MISS, {"success": True, "result": "KTX 101", "error": None}]
        )
        on_success = Mock()

        result, reporter, statuses = _run(handler, on_success=on_success)

        assert result == {
            "status": "success",
            "attempts": 3,
            "train_info": "KTX 101",
            "waiting": False,
            "reported": True,
        }
        assert statuses == ["running", "success"]
        assert reporter.send.call_args.kwargs["train_info"] == "KTX 101"
        # 보고 전에 결과를 남길 수 있도록 결과와 함께 호출
        on_success.assert_called_once_with(
            train_info="KTX 101", attempts=3, waiting=False
        )
        kwargs = handler.reserve_single_attempt.call_args.kwargs
        assert kwargs["depTime"] == "090000"
        assert kwargs["maxDepTime"] == "1200"
        assert kwargs["trainType"] == "109"  # TrainType.ALL
        assert kwargs["special"] == "SPECIAL_ONLY"

    def test_duplicate_reservation_counts_as_success(self):
        handler = _handler(
            [{"success": True, "result": "duplicate_reservation", "error": None}]
        )
        result, _, statuses = _run(handler)
        assert result["status"] == "success"
        assert "장바구니" in result["train_info"]

    def test_login_failure(self):
        result, _, statuses = _run(_handler([], login=False))
        assert result["status"] == "error"
        assert statuses == ["error"]

    def test_max_attempts_reports_failed_with_progress(self):
        handler = _handler([MISS] * 5)
        result, reporter, statuses = _run(handler, max_attempts=5, progress_every=2)
        assert result == {"status": "failed", "attempts": 5}
        assert statuses == ["running", "progress", "progress", "failed"]
        assert reporter.send.call_args.kwargs["attempts"] == 5

    def test_relogin_on_session_error(self):
        session_error = {
            "success": False,
            "result": None,
            "error": "로그인이 필요합니다",
        }
        handler = _handler(
            [session_error, {"success": True, "result": "KTX", "error": None}]
        )
        result, _, _ = _run(handler)
        assert result["status"] == "success"
        assert handler.login.call_count == 2

    def test_relogin_after_consecutive_errors(self):
        weird = {"success": False, "result": None, "error": "HTTP 500"}
        handler = _handler([weird] * 3 + [MISS])
        _run(handler, max_attempts=4, relogin_after_errors=3)
        assert handler.login.call_count == 2

    def test_relogin_failure_reports_error(self):
        session_error = {"success": False, "result": None, "error": "session expired"}
        handler = _handler([session_error], login=[True, False])
        result, _, statuses = _run(handler)
        assert result["status"] == "error"
        assert statuses == ["running", "error"]

    def test_exception_in_attempt_is_contained(self):
        handler = _handler([RuntimeError("boom"), MISS])
        result, _, _ = _run(handler, max_attempts=2)
        assert result["status"] == "failed"

    def test_should_stop(self):
        handler = _handler([MISS] * 10)
        calls = iter([False, False, True])
        result, _, statuses = _run(handler, should_stop=lambda: next(calls))
        assert result == {"status": "stopped", "attempts": 2}
        assert statuses == ["running"]

    def test_success_report_retried_until_delivered(self):
        """표는 이미 잡혔으므로 웹 서버가 잠시 응답하지 않아도 성공 보고를 다시 보냄"""
        handler = _handler([{"success": True, "result": "KTX 101", "error": None}])
        reporter = Mock(rejected=False)
        reporter.send.side_effect = lambda status, **kw: status != "success" or (
            reporter.send.call_count >= 4
        )
        slept = []

        result = run_reservation(
            SPEC, reporter, handler_factory=lambda: handler, sleep=slept.append
        )

        assert result["status"] == "success"
        assert result["reported"] is True
        statuses = [c.args[0] for c in reporter.send.call_args_list]
        assert statuses == ["running", "success", "success", "success"]
        assert slept == [2.0, 4.0]  # 간격을 늘려 가며

    def test_success_report_gives_up_after_window(self):
        from core.runner import SUCCESS_REPORT_WINDOW_SECONDS

        handler = _handler([{"success": True, "result": "KTX 101", "error": None}])
        reporter = Mock(rejected=False)
        reporter.send.side_effect = lambda status, **kw: status != "success"
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        result = run_reservation(
            SPEC,
            reporter,
            handler_factory=lambda: handler,
            sleep=sleep,
            clock=lambda: now[0],
        )

        assert result["reported"] is False  # 결과는 on_success로 남겨 둔 값으로 복구
        assert (
            SUCCESS_REPORT_WINDOW_SECONDS <= now[0] < SUCCESS_REPORT_WINDOW_SECONDS + 60
        )

    def test_success_report_stops_when_rejected(self):
        handler = _handler([{"success": True, "result": "KTX 101", "error": None}])
        reporter = Mock(rejected=False)

        def send(status, **kw):
            if status == "success":
                reporter.rejected = True  # 404: 웹 서버가 모르는 예약
                return False
            return True

        reporter.send.side_effect = send
        result = run_reservation(
            SPEC, reporter, handler_factory=lambda: handler, sleep=Mock()
        )
        assert result["reported"] is False
        assert [c.args[0] for c in reporter.send.call_args_list] == [
            "running",
            "success",
        ]

    def test_max_duration_reports_failed(self):
        """최대 실행 시간을 넘기면 failed (Celery threads 풀은 시간 제한이 없음)"""
        handler = _handler([MISS] * 100)
        reporter = Mock()
        now = [1000.0]

        def sleep(seconds):
            now[0] += seconds

        result = run_reservation(
            {**SPEC, "max_duration": 10},
            reporter,
            handler_factory=lambda: handler,
            sleep=sleep,
            interval=2.0,
            clock=lambda: now[0],
        )

        # 2초 간격으로 5번 시도하면 10초 경과
        assert result == {"status": "failed", "attempts": 5, "timed_out": True}
        assert handler.reserve_single_attempt.call_count == 5
        last = reporter.send.call_args
        assert last.args[0] == "failed"
        assert last.kwargs == {"message": MAX_DURATION_MESSAGE, "attempts": 5}

    def test_without_max_duration_runs_until_max_attempts(self):
        handler = _handler([MISS] * 3)
        clock = Mock(side_effect=AssertionError("clock must not be used"))
        result, _, statuses = _run(handler, max_attempts=3, clock=clock)
        assert result == {"status": "failed", "attempts": 3}
        assert statuses[-1] == "failed"

    def test_fatal_error_stops_immediately(self):
        """역 이름 오류·지난 날짜 등은 재시도하지 않고 바로 failed"""
        fatal = {
            "success": False,
            "result": None,
            "error": "존재하지 않는 역입니다",
            "fatal": True,
        }
        handler = _handler([fatal, MISS, MISS])
        result, reporter, statuses = _run(handler)
        assert result["status"] == "failed"
        assert statuses == ["running", "failed"]
        assert reporter.send.call_args.kwargs["message"] == "존재하지 않는 역입니다"
        assert handler.reserve_single_attempt.call_count == 1

    @pytest.mark.parametrize(
        "seat_type, option",
        [
            ("general", "GENERAL_FIRST"),
            ("general_only", "GENERAL_ONLY"),
            ("special", "SPECIAL_FIRST"),
            ("special_only", "SPECIAL_ONLY"),
        ],
    )
    def test_seat_type_is_passed_to_korail(self, seat_type, option):
        handler = _handler([{"success": True, "result": "KTX", "error": None}])
        reporter = Mock()
        run_reservation(
            {**SPEC, "seat_type": seat_type},
            reporter,
            handler_factory=lambda: handler,
            sleep=lambda _: None,
        )
        assert handler.reserve_single_attempt.call_args.kwargs["special"] == option

    def test_login_failure_reason_is_reported(self):
        handler = _handler([], login=False)
        handler.loginError = "코레일 서버가 사유 없이 로그인을 거부했습니다."
        result, reporter, _ = _run(handler)
        assert "사유 없이" in reporter.send.call_args.kwargs["message"]
        assert "사유 없이" in result["message"]

    @pytest.mark.parametrize("login", [True, False])
    def test_korail_session_is_closed(self, login):
        handler = _handler([MISS], login=login)
        _run(handler, max_attempts=1)
        handler.close.assert_called_once()

    def test_stops_when_running_report_is_rejected(self):
        """웹 서버가 모르는 예약이면 코레일 조회 없이 종료"""
        handler = _handler([MISS] * 5)
        reporter = Mock(rejected=False)

        def send(status, **kw):
            reporter.rejected = True
            return False

        reporter.send.side_effect = send
        result = run_reservation(
            SPEC,
            reporter,
            handler_factory=lambda: handler,
            sleep=lambda _: None,
        )
        assert result == {"status": "rejected", "attempts": 0}
        handler.reserve_single_attempt.assert_not_called()
        handler.close.assert_called_once()

    def test_stops_when_progress_report_is_rejected(self):
        """취소·만료된 예약은 다음 진행 보고에서 멈춤 (고아 워커 방지)"""
        handler = _handler([MISS] * 100)
        reporter = Mock(rejected=False)

        def send(status, **kw):
            if status == "progress":
                reporter.rejected = True
            return True

        reporter.send.side_effect = send
        result = run_reservation(
            SPEC,
            reporter,
            handler_factory=lambda: handler,
            sleep=lambda _: None,
            progress_every=5,
        )
        assert result == {"status": "rejected", "attempts": 5}
        assert handler.reserve_single_attempt.call_count == 5

    def test_transient_report_failure_keeps_running(self):
        """웹 서버 재시작 등 일시 오류로는 멈추지 않음"""
        handler = _handler([MISS] * 10)
        reporter = Mock(rejected=False)
        reporter.send.return_value = False
        result = run_reservation(
            SPEC,
            reporter,
            handler_factory=lambda: handler,
            sleep=lambda _: None,
            max_attempts=10,
            progress_every=2,
        )
        assert result["status"] == "failed"
        assert handler.reserve_single_attempt.call_count == 10


BLOCKED = {"success": False, "result": None, "error": "blocked", "blocked": True}


class FakeTime:
    """sleep이 시계를 진행시키는 가짜 시간 (출구 상태와 실행 시간이 같은 시계를 씀)"""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class TestEgress:
    """출구 차단 대기와 출구별 요청 간격"""

    def _run(self, handler, gate=None, spec=SPEC, **kwargs):
        fake = FakeTime()
        gate = gate or MemoryGate(clock=fake.clock)
        reporter = Mock()
        reporter.rejected = False
        result = run_reservation(
            spec,
            reporter,
            handler_factory=lambda: handler,
            sleep=fake.sleep,
            clock=fake.clock,
            gate=gate,
            **kwargs,
        )
        return result, reporter, gate, fake

    def test_block_pauses_egress_and_retries_after_it(self):
        handler = _handler(
            [MISS, BLOCKED, {"success": True, "result": "KTX 101", "error": None}]
        )
        result, reporter, gate, fake = self._run(handler, interval=2.0)

        assert result["status"] == "success"
        assert result["attempts"] == 3
        # 차단 보고 후 대기 시간이 지날 때까지 다음 시도를 하지 않음
        assert sum(fake.slept) >= BLOCK_BACKOFF_SECONDS[0]
        progress = [c for c in reporter.send.call_args_list if c.args[0] == "progress"]
        assert "차단" in progress[0].kwargs["message"]
        # 차단은 오류가 아니므로 재로그인하지 않음
        assert handler.login.call_count == 1

    def test_waits_for_existing_block_before_login(self):
        fake_gate = MemoryGate()
        fake_gate.blocked_for = Mock(side_effect=[30.0, 0.0, 0.0])
        handler = _handler([{"success": True, "result": "KTX 1", "error": None}])
        result, _, _, fake = self._run(handler, gate=fake_gate)

        assert result["status"] == "success"
        assert fake.slept[0] == 5.0  # 취소 확인 간격으로 나눠 기다림
        assert fake_gate.blocked_for.call_count == 3

    def test_blocked_login_waits_and_logs_in_again(self):
        handler = _handler([{"success": True, "result": "KTX 1", "error": None}])
        handler.login = Mock(side_effect=[False, True])
        handler.loginBlocked = True
        result, reporter, _, fake = self._run(handler)

        assert result["status"] == "success"
        assert handler.login.call_count == 2
        assert sum(fake.slept) >= BLOCK_BACKOFF_SECONDS[0]
        assert [c.args[0] for c in reporter.send.call_args_list][0] == "progress"

    def test_wrong_password_is_not_treated_as_block(self):
        handler = _handler([], login=False)
        handler.loginBlocked = False
        result, _, _, fake = self._run(handler)
        assert result["status"] == "error"
        assert fake.slept == []

    def test_blocked_relogin_waits_instead_of_failing(self):
        session_error = {"success": False, "result": None, "error": "session expired"}
        handler = _handler(
            [session_error, MISS, {"success": True, "result": "KTX 1", "error": None}]
        )
        handler.login = Mock(side_effect=[True, False])
        handler.loginBlocked = True
        result, _, _, fake = self._run(handler)

        assert result["status"] == "success"
        assert sum(fake.slept) >= BLOCK_BACKOFF_SECONDS[0]

    def test_cancel_during_block_wait(self):
        handler = _handler([BLOCKED, MISS])
        stops = iter([False, False] + [True] * 10)
        result, _, _, fake = self._run(handler, should_stop=lambda: next(stops))

        assert result["status"] == "stopped"
        assert sum(fake.slept) < BLOCK_BACKOFF_SECONDS[0]

    def test_max_duration_during_block_wait(self):
        handler = _handler([BLOCKED, MISS])
        spec = {**SPEC, "max_duration": 60}
        result, reporter, _, _ = self._run(handler, spec=spec)

        assert result["status"] == "failed"
        assert result["timed_out"] is True
        assert reporter.send.call_args.kwargs["message"] == MAX_DURATION_MESSAGE

    @pytest.mark.parametrize("wait", ["block", "slot"])
    def test_deadline_is_rechecked_after_the_last_wait(self, wait):
        """마지막 대기 중에 최대 실행 시간이 지나면 로그인·검색을 시작하지 않음"""
        gate = MemoryGate()
        if wait == "block":
            gate.blocked_for = Mock(side_effect=[5.0, 0.0])
        else:
            gate.reserve_slot = Mock(return_value=5.0)
        handler = _handler([MISS])
        spec = {**SPEC, "max_duration": 3}
        result, reporter, _, _ = self._run(handler, gate=gate, spec=spec)

        assert result["status"] == "failed"
        assert result["timed_out"] is True
        handler.login.assert_not_called()
        assert reporter.send.call_args.kwargs["message"] == MAX_DURATION_MESSAGE

    def test_long_block_keeps_reservation_alive(self):
        """30분 무응답 정리에 걸리지 않도록 대기 중에도 진행 보고"""
        handler = _handler([BLOCKED] * 5 + [MISS])
        result, reporter, _, _ = self._run(handler, max_attempts=6)

        progress = [c for c in reporter.send.call_args_list if c.args[0] == "progress"]
        assert len(progress) > 5  # 차단 보고 5번 + 대기 중 보고
        assert result["status"] == "failed"

    def test_requests_follow_egress_pacing(self):
        fake = FakeTime()
        gate = MemoryGate(rpm=6, clock=fake.clock)  # 출구 전체 10초에 1번
        gate.reserve_slot()  # 다른 예약이 방금 순서를 잡음
        handler = _handler([MISS, MISS, MISS])
        reporter = Mock()
        reporter.rejected = False
        run_reservation(
            SPEC,
            reporter,
            handler_factory=lambda: handler,
            sleep=fake.sleep,
            clock=fake.clock,
            gate=gate,
            max_attempts=3,
            interval=2.0,
        )
        # 첫 시도는 10초 기다림, 이후에도 간격 10초를 지킴 (예약 간격 2초보다 김)
        assert fake.now - 1000.0 >= 30.0

    def test_default_handler_uses_spec_egress(self, monkeypatch):
        created = {}

        class Handler:
            def __init__(self, proxy_url="", egress_id="", *, device=None):
                created.update(proxy_url=proxy_url, egress_id=egress_id)

            def login(self, *_):
                return False

            loginError = "x"
            loginBlocked = False

            def close(self):
                pass

        monkeypatch.setattr("core.runner.ReserveHandler", Handler)
        spec = {**SPEC, "egress_id": "home1", "egress_proxy": "socks5h://h:1"}
        run_reservation(spec, Mock(rejected=False), sleep=lambda _: None)
        assert created == {"proxy_url": "socks5h://h:1", "egress_id": "home1"}


class TestCallbackReporter:
    def test_payload(self):
        reporter = CallbackReporter("http://cb/internal/events", "r1", "tok")
        response = Mock()
        reporter.session.post = Mock(return_value=response)

        assert reporter.send("success", attempts=3, train_info="KTX")

        url = reporter.session.post.call_args.args[0]
        payload = reporter.session.post.call_args.kwargs["json"]
        assert url == "http://cb/internal/events"
        assert payload == {
            "reservation_id": "r1",
            "token": "tok",
            "status": "success",
            "attempts": 3,
            "train_info": "KTX",
        }

    @pytest.mark.parametrize("status_code", [403, 404])
    def test_rejected_status_codes(self, status_code, monkeypatch):
        monkeypatch.setattr("core.runner.time.sleep", lambda _: None)
        reporter = CallbackReporter("http://cb", "r1", "tok")
        reporter.session.post = Mock(return_value=Mock(status_code=status_code))

        assert reporter.send("progress", attempts=20) is False
        assert reporter.rejected is True
        assert reporter.session.post.call_count == 1  # 재시도하지 않음

    @pytest.mark.parametrize(
        "body, rejected",
        [({"applied": False}, True), ({"applied": True}, False), (ValueError(), False)],
    )
    def test_applied_false_is_rejection(self, body, rejected):
        reporter = CallbackReporter("http://cb", "r1", "tok")
        response = Mock(status_code=200)
        if isinstance(body, Exception):
            response.json.side_effect = body
        else:
            response.json.return_value = body
        reporter.session.post = Mock(return_value=response)

        assert reporter.send("progress", attempts=20) is True
        assert reporter.rejected is rejected

    def test_server_error_is_not_rejection(self, monkeypatch):
        monkeypatch.setattr("core.runner.time.sleep", lambda _: None)
        reporter = CallbackReporter("http://cb", "r1", "tok")
        response = Mock(status_code=503)
        response.raise_for_status.side_effect = requests.HTTPError("503")
        reporter.session.post = Mock(return_value=response)

        assert reporter.send("progress", attempts=20) is False
        assert reporter.rejected is False
        assert reporter.session.post.call_count == 3

    def test_retries(self, monkeypatch):
        monkeypatch.setattr("core.runner.time.sleep", lambda _: None)
        reporter = CallbackReporter("http://cb", "r1", "tok")
        reporter.session.post = Mock(
            side_effect=[requests.ConnectionError("x"), Mock()]
        )
        assert reporter.send("progress", attempts=50)
        assert reporter.session.post.call_count == 2
