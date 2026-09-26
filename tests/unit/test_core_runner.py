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

    def test_retries(self, monkeypatch):
        monkeypatch.setattr("core.runner.time.sleep", lambda _: None)
        reporter = CallbackReporter("http://cb", "r1", "tok")
        reporter.session.post = Mock(
            side_effect=[requests.ConnectionError("x"), Mock()]
        )
        assert reporter.send("progress", attempts=50)
        assert reporter.session.post.call_count == 2
