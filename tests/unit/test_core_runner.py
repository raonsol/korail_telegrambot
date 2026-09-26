"""워커 공통 예약 루프와 콜백 보고"""

from unittest.mock import Mock

import pytest
import requests

from core.runner import CallbackReporter, run_reservation

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

        assert result == {"status": "success", "attempts": 3, "train_info": "KTX 101"}
        assert statuses == ["running", "success"]
        assert reporter.send.call_args.kwargs["train_info"] == "KTX 101"
        on_success.assert_called_once()
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
            SPEC, reporter, handler_factory=lambda: handler, sleep=lambda _: None
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
