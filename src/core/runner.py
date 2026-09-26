"""워커 공통 예약 루프

subprocess 워커(``telegramBot.worker``)와 Celery 태스크(``telegramBot.tasks``)가
같은 로직으로 예약을 시도하고, 결과를 ``/internal/events``로 보고합니다.
이 모듈은 DB/웹 계층을 import하지 않습니다(워커 프로세스는 가볍게 유지).
"""

import logging
import time
from typing import Callable, Optional

import requests
from pykorail import ReserveOption, TrainType

from telegramBot.korail_client import ReserveHandler

logger = logging.getLogger(__name__)

TRAIN_TYPES = {"KTX": TrainType.KTX, "ALL": TrainType.ALL}
SEAT_TYPES = {
    "general": ReserveOption.GENERAL_FIRST,
    "general_only": ReserveOption.GENERAL_ONLY,
    "special": ReserveOption.SPECIAL_FIRST,
    "special_only": ReserveOption.SPECIAL_ONLY,
}

# reserve_single_attempt가 돌려주는 '정상적인 실패' (재로그인 불필요)
EXPECTED_MISSES = ("No trains available", "All trains sold out")

MAX_ATTEMPTS_MESSAGE = "최대 시도 횟수를 초과하여 예약이 중단되었습니다."

# 웹 서버가 이 예약을 받아들이지 않는 응답 (예약 없음 / 토큰 불일치)
# 서버 재시작 등 일시적인 연결 실패와 달리 다시 보내도 결과가 같다
REJECTED_STATUS_CODES = (403, 404)


class CallbackReporter:
    """예약 상태를 웹 서버에 보고"""

    def __init__(self, callback_url: str, reservation_id: str, token: str):
        self.callback_url = callback_url
        self.reservation_id = reservation_id
        self.token = token
        self.session = requests.Session()
        # 웹 서버가 예약을 모르거나(404/403) 이미 종료된 예약(applied=false)이라고
        # 응답하면 True. 워커는 이 값을 보고 예약 시도를 멈춘다
        self.rejected = False

    def send(
        self,
        status: str,
        message: Optional[str] = None,
        attempts: Optional[int] = None,
        train_info: Optional[str] = None,
        retries: int = 3,
    ) -> bool:
        payload = {
            "reservation_id": self.reservation_id,
            "token": self.token,
            "status": status,
        }
        if message is not None:
            payload["message"] = message
        if attempts is not None:
            payload["attempts"] = attempts
        if train_info is not None:
            payload["train_info"] = train_info

        for attempt in range(retries):
            try:
                response = self.session.post(
                    self.callback_url, json=payload, timeout=10
                )
                if response.status_code in REJECTED_STATUS_CODES:
                    self._reject(status, f"HTTP {response.status_code}")
                    return False
                response.raise_for_status()
                if _not_applied(response):
                    self._reject(status, "reservation already finished")
                return True
            except requests.RequestException as e:
                if attempt == retries - 1:
                    logger.error(f"Failed to report '{status}': {e}")
                else:
                    time.sleep(1)
        return False

    def _reject(self, status: str, reason: str) -> None:
        self.rejected = True
        logger.warning(
            f"Report '{status}' for {self.reservation_id} rejected by server ({reason})"
        )


def _not_applied(response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("applied") is False


def _rejected(reporter) -> bool:
    return getattr(reporter, "rejected", False) is True


def build_reporter(spec: dict) -> CallbackReporter:
    return CallbackReporter(
        spec["callback_url"], spec["reservation_id"], spec["callback_token"]
    )


def run_reservation(
    spec: dict,
    reporter: CallbackReporter,
    should_stop: Callable[[], bool] = lambda: False,
    on_success: Callable[[], None] = lambda: None,
    max_attempts: int = 1000,
    interval: float = 2.0,
    progress_every: int = 20,
    relogin_after_errors: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    handler_factory: Callable[[], ReserveHandler] = ReserveHandler,
) -> dict:
    """예약이 성공하거나 최대 시도 횟수에 도달할 때까지 반복

    Args:
        spec: 예약 명세 (ReservationService._build_spec 참고)
        reporter: 상태 보고 객체
        should_stop: True를 반환하면 즉시 종료 (Celery 중복 실행 방지용)
        on_success: 성공 직후 호출 (Celery의 Redis 완료 표시용)

    Returns:
        dict: {"status": "success"|"failed"|"error"|"stopped"|"rejected", ...}
            rejected: 웹 서버가 보고를 거부함 (취소·만료됐거나 모르는 예약) → 즉시 종료
    """
    handler = handler_factory()
    try:
        return _run_attempts(
            spec,
            handler,
            reporter,
            should_stop,
            on_success,
            max_attempts,
            interval,
            progress_every,
            relogin_after_errors,
            sleep,
        )
    finally:
        # 코레일 HTTP 세션 정리 (pykorail)
        close = getattr(handler, "close", None)
        if callable(close):
            close()


def _login_failure_message(handler) -> str:
    reason = getattr(handler, "loginError", "") or ""
    return f"코레일 로그인에 실패했습니다. {reason}".strip()


def _run_attempts(
    spec,
    handler,
    reporter,
    should_stop,
    on_success,
    max_attempts,
    interval,
    progress_every,
    relogin_after_errors,
    sleep,
) -> dict:
    korail_id = spec["korail_id"]
    korail_pw = spec["korail_pw"]
    train_type = TRAIN_TYPES.get(spec["train_type"], TrainType.KTX)
    seat_type = SEAT_TYPES.get(spec["seat_type"], ReserveOption.GENERAL_FIRST)

    if not handler.login(korail_id, korail_pw):
        message = _login_failure_message(handler)
        reporter.send("error", message=message)
        return {"status": "error", "message": message}

    reporter.send("running", attempts=0)
    if _rejected(reporter):
        return {"status": "rejected", "attempts": 0}

    consecutive_errors = 0
    for attempt in range(1, max_attempts + 1):
        if should_stop():
            return {"status": "stopped", "attempts": attempt - 1}

        try:
            result = handler.reserve_single_attempt(
                depDate=spec["dep_date"],
                srcLocate=spec["src_station"],
                dstLocate=spec["dst_station"],
                depTime=f"{spec['dep_time']}00",
                trainType=train_type,
                special=seat_type,
                maxDepTime=spec["max_dep_time"],
            )
        except Exception as e:  # reserve_single_attempt는 보통 예외를 삼키지만 방어
            result = {"success": False, "result": None, "error": str(e)}

        if result["success"]:
            if result["result"] == "duplicate_reservation":
                train_info = "이미 동일한 예약이 존재합니다. 장바구니를 확인해주세요."
            else:
                train_info = str(result["result"])
            on_success()
            reporter.send("success", attempts=attempt, train_info=train_info)
            return {"status": "success", "attempts": attempt, "train_info": train_info}

        error = result.get("error") or ""
        if result.get("fatal"):
            # 역 이름 오류, 지난 출발일 등은 재시도해도 결과가 같음
            reporter.send("failed", message=error, attempts=attempt)
            return {"status": "failed", "message": error, "attempts": attempt}
        if error and error not in EXPECTED_MISSES:
            consecutive_errors += 1
            if attempt % 10 == 0:
                logger.warning(f"Attempt {attempt} failed: {error}")
            needs_login = any(
                word in error.lower() for word in ("login", "session", "로그인")
            )
            if needs_login or consecutive_errors >= relogin_after_errors:
                logger.info("Re-logging in to Korail")
                if not handler.login(korail_id, korail_pw):
                    message = "세션 오류로 재로그인에 실패하여 예약이 중단되었습니다."
                    reason = getattr(handler, "loginError", "")
                    if reason:
                        message = f"{message} ({reason})"
                    reporter.send("error", message=message, attempts=attempt)
                    return {"status": "error", "message": message}
                consecutive_errors = 0
        else:
            consecutive_errors = 0

        if attempt % progress_every == 0:
            reporter.send("progress", attempts=attempt)
            if _rejected(reporter):
                # 취소·만료됐거나 웹 서버가 모르는 예약 (예: DB 초기화 후 남은 워커)
                logger.warning(f"Stopping reservation after {attempt} attempts")
                return {"status": "rejected", "attempts": attempt}

        sleep(interval)

    reporter.send("failed", message=MAX_ATTEMPTS_MESSAGE, attempts=max_attempts)
    return {"status": "failed", "attempts": max_attempts}
