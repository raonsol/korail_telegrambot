"""워커 공통 예약 루프

subprocess 워커(``telegramBot.worker``)와 Celery 태스크(``telegramBot.tasks``)가
같은 로직으로 예약을 시도하고, 결과를 ``/internal/events``로 보고합니다.
이 모듈은 DB/웹 계층을 import하지 않습니다(워커 프로세스는 가볍게 유지).
"""

import json
import logging
import os
import time
from typing import Callable, Optional

import requests
from pykorail import ReserveOption, TrainType

from core.egress import EgressGate, NullGate
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

DUPLICATE_RESERVATION_MESSAGE = (
    "이미 동일한 예약이 존재합니다. 장바구니를 확인해주세요."
)
DUPLICATE_WAITLIST_MESSAGE = (
    "이미 같은 열차에 예약대기를 신청해 두었습니다. 예약 승차권 조회에서 확인해주세요."
)

MAX_ATTEMPTS_MESSAGE = "최대 시도 횟수를 초과하여 예약이 중단되었습니다."
MAX_DURATION_MESSAGE = "최대 실행 시간을 초과하여 예약이 중단되었습니다."

# 예약 성공 보고는 웹 서버가 잠시 응답하지 않아도(재시작 등) 이 시간 동안 다시 보냄.
# 표는 이미 잡혔으므로 사용자가 결제 기한 안에 알아야 함
SUCCESS_REPORT_WINDOW_SECONDS = 600
SUCCESS_REPORT_MAX_BACKOFF_SECONDS = 30

# 출구(차단 대기·요청 순서)를 기다리는 동안 취소·최대 실행 시간을 확인하는 간격
WAIT_STEP_SECONDS = 5.0
# 차단 대기 중에도 이 간격으로 진행 보고 (웹 서버의 30분 무응답 정리에 걸리지 않도록)
WAIT_KEEPALIVE_SECONDS = 300.0

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
        waiting: bool = False,
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
        if waiting:
            payload["waiting"] = True

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


def result_file(directory: str, reservation_id: str) -> str:
    """subprocess 워커가 예약 성공 결과를 남기는 파일 (웹 서버가 보고를 못 받았을 때 복구용)"""
    return os.path.join(directory, f"result_{reservation_id}.json")


def write_result_file(
    path: str, train_info: str, attempts: int, waiting: bool = False
) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(
            {"train_info": train_info, "attempts": attempts, "waiting": waiting}, f
        )
    os.replace(tmp, path)  # 웹 서버가 쓰다 만 파일을 읽지 않도록


def read_result_file(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("train_info") else None


def build_reporter(spec: dict) -> CallbackReporter:
    return CallbackReporter(
        spec["callback_url"], spec["reservation_id"], spec["callback_token"]
    )


def run_reservation(
    spec: dict,
    reporter: CallbackReporter,
    should_stop: Callable[[], bool] = lambda: False,
    on_success: Callable[..., None] = lambda **_: None,
    max_attempts: int = 1000,
    interval: float = 2.0,
    progress_every: int = 20,
    relogin_after_errors: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    handler_factory: Optional[Callable[[], ReserveHandler]] = None,
    clock: Callable[[], float] = time.monotonic,
    gate: Optional[EgressGate] = None,
) -> dict:
    """예약이 성공하거나 최대 시도 횟수에 도달할 때까지 반복

    Args:
        spec: 예약 명세 (ReservationService._build_spec 참고)
        reporter: 상태 보고 객체
        should_stop: True를 반환하면 즉시 종료 (Celery 중복 실행 방지용)
        on_success: 성공 직후, 보고 전에 ``on_success(train_info=..., attempts=..., waiting=...)`` 호출
            (waiting: 좌석 대신 예약대기를 신청함).
            보고가 끝내 전달되지 않아도 웹 서버가 결과를 복구할 수 있도록 기록하는 용도
            (subprocess: 결과 파일, Celery: Redis 상태)
        clock: 경과 시간 측정용. ``spec["max_duration"]``(초)을 넘기면 ``failed``로 끝냄
            (Celery threads 풀은 태스크 시간 제한을 적용하지 않으므로 루프에서 직접 확인)
        gate: 이 예약의 출구(``spec["egress_id"]``) 상태. 매 시도 전에 차단 대기와
            출구별 요청 순서를 기다리고, 코레일 차단 응답을 받으면 출구 전체를 쉬게 함

    Returns:
        dict: {"status": "success"|"failed"|"error"|"stopped"|"rejected", ...}
            success에는 성공 보고가 전달됐는지 ``reported``가 함께 들어감
            rejected: 웹 서버가 보고를 거부함 (취소·만료됐거나 모르는 예약) → 즉시 종료
    """
    if handler_factory is None:
        handler = ReserveHandler(
            proxy_url=spec.get("egress_proxy") or "",
            egress_id=spec.get("egress_id") or "",
            # 웹 서버가 발급한 기기 신원 (재로그인해도 같은 기기로 접속)
            device=spec.get("korail_device"),
        )
    else:
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
            _deadline(spec, clock),
            clock,
            gate or NullGate(),
        )
    finally:
        # 코레일 HTTP 세션 정리 (pykorail)
        close = getattr(handler, "close", None)
        if callable(close):
            close()


def _deadline(spec: dict, clock: Callable[[], float]) -> Callable[[], bool]:
    """최대 실행 시간이 지났는지 알려주는 함수 (max_duration이 없으면 항상 False)"""
    max_duration = spec.get("max_duration")
    if not max_duration:
        return lambda: False
    started = clock()
    return lambda: clock() - started >= max_duration


def _report_success(reporter, attempts, train_info, waiting, sleep, clock) -> bool:
    """성공 보고가 전달되거나 거부될 때까지 간격을 늘려 가며 다시 보냄"""
    deadline = clock() + SUCCESS_REPORT_WINDOW_SECONDS
    backoff = 2.0
    extra = {"waiting": True} if waiting else {}
    while True:
        if reporter.send("success", attempts=attempts, train_info=train_info, **extra):
            return True
        if _rejected(reporter) or clock() >= deadline:
            logger.error("Success report was not delivered; left for server recovery")
            return False
        sleep(backoff)
        backoff = min(backoff * 2, SUCCESS_REPORT_MAX_BACKOFF_SECONDS)


def _blocked(handler) -> bool:
    """마지막 로그인이 코레일 차단 응답으로 실패했는지"""
    return getattr(handler, "loginBlocked", False) is True


def _blocked_message(pause: float) -> str:
    minutes = max(1, round(pause / 60))
    return (
        f"코레일 서버가 요청을 일시적으로 차단해 약 {minutes}분 동안 기다린 뒤 "
        "다시 시도합니다."
    )


def _report_block(gate, spec, reporter, attempts) -> Optional[dict]:
    """코레일 차단 응답 기록 (출구 전체가 쉼). 웹 서버가 보고를 거부하면 결과 반환"""
    pause = gate.report_block()
    logger.warning(
        f"Korail blocked egress {spec.get('egress_id') or 'direct'}; "
        f"pausing it for {pause:.0f}s"
    )
    reporter.send("progress", message=_blocked_message(pause), attempts=attempts)
    if _rejected(reporter):
        return {"status": "rejected", "attempts": attempts}
    return None


def _wait_for_egress(
    gate, reporter, should_stop, expired, sleep, clock, attempts
) -> Optional[dict]:
    """출구 차단 대기와 요청 순서를 기다림. 그동안 멈춰야 하면 결과를 반환

    대기 중에도 취소(should_stop)와 최대 실행 시간을 확인하고, 오래 기다리면
    진행 보고를 보내 웹 서버가 무응답 예약으로 정리하지 않게 한다.
    """

    def interrupted() -> Optional[dict]:
        if should_stop():
            return {"status": "stopped", "attempts": attempts}
        if expired():
            reporter.send("failed", message=MAX_DURATION_MESSAGE, attempts=attempts)
            return {"status": "failed", "attempts": attempts, "timed_out": True}
        return None

    waited = False
    last_report = None
    while True:
        remaining = gate.blocked_for()
        if remaining <= 0:
            break
        stop = interrupted()
        if stop:
            return stop
        waited = True
        if last_report is None:
            last_report = clock()
        elif clock() - last_report >= WAIT_KEEPALIVE_SECONDS:
            reporter.send("progress", attempts=attempts)
            if _rejected(reporter):
                return {"status": "rejected", "attempts": attempts}
            last_report = clock()
        sleep(min(remaining, WAIT_STEP_SECONDS))

    delay = gate.reserve_slot()
    while delay > 0:
        stop = interrupted()
        if stop:
            return stop
        waited = True
        step = min(delay, WAIT_STEP_SECONDS)
        sleep(step)
        delay -= step
    # 마지막 대기 중에 취소됐거나 최대 실행 시간이 지났으면 요청하지 않음
    return interrupted() if waited else None


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
    expired,
    clock,
    gate,
) -> dict:
    korail_id = spec["korail_id"]
    korail_pw = spec["korail_pw"]
    train_type = TRAIN_TYPES.get(spec["train_type"], TrainType.KTX)
    seat_type = SEAT_TYPES.get(spec["seat_type"], ReserveOption.GENERAL_FIRST)

    def wait_for_egress(attempts: int) -> Optional[dict]:
        return _wait_for_egress(
            gate, reporter, should_stop, expired, sleep, clock, attempts
        )

    # 로그인도 출구 차단 중에는 하지 않음. 차단 응답이면 차단이 풀릴 때까지 기다렸다 다시 로그인
    while True:
        stop = wait_for_egress(0)
        if stop:
            return stop
        if handler.login(korail_id, korail_pw):
            break
        if not _blocked(handler):
            message = _login_failure_message(handler)
            reporter.send("error", message=message)
            return {"status": "error", "message": message}
        stop = _report_block(gate, spec, reporter, 0)
        if stop:
            return stop

    reporter.send("running", attempts=0)
    if _rejected(reporter):
        return {"status": "rejected", "attempts": 0}

    consecutive_errors = 0
    for attempt in range(1, max_attempts + 1):
        if should_stop():
            return {"status": "stopped", "attempts": attempt - 1}
        if expired():
            reporter.send("failed", message=MAX_DURATION_MESSAGE, attempts=attempt - 1)
            return {"status": "failed", "attempts": attempt - 1, "timed_out": True}
        stop = wait_for_egress(attempt - 1)
        if stop:
            return stop

        try:
            result = handler.reserve_single_attempt(
                depDate=spec["dep_date"],
                srcLocate=spec["src_station"],
                dstLocate=spec["dst_station"],
                depTime=f"{spec['dep_time']}00",
                trainType=train_type,
                special=seat_type,
                maxDepTime=spec["max_dep_time"],
                allowWaitlist=spec.get("allow_waitlist") is True,
            )
        except Exception as e:  # reserve_single_attempt는 보통 예외를 삼키지만 방어
            result = {"success": False, "result": None, "error": str(e)}

        if result["success"]:
            waiting = result.get("waiting") is True
            if result["result"] == "duplicate_reservation":
                train_info = (
                    DUPLICATE_WAITLIST_MESSAGE
                    if waiting
                    else DUPLICATE_RESERVATION_MESSAGE
                )
            else:
                train_info = str(result["result"])
            on_success(train_info=train_info, attempts=attempt, waiting=waiting)
            reported = _report_success(
                reporter, attempt, train_info, waiting, sleep, clock
            )
            return {
                "status": "success",
                "attempts": attempt,
                "train_info": train_info,
                "waiting": waiting,
                "reported": reported,
            }

        error = result.get("error") or ""
        if result.get("blocked"):
            # 같은 출구로 계속 요청하면 차단이 길어지므로 출구 전체를 쉬게 함
            # (다른 출구로 옮기지 않음: 한 계정이 여러 IP에서 보이면 그 자체로 매크로 신호)
            stop = _report_block(gate, spec, reporter, attempt)
            if stop:
                return stop
            continue
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
                    if _blocked(handler):
                        # 다음 시도 전에 차단이 풀릴 때까지 기다림 (연속 오류 수는 유지되므로
                        # 차단이 풀린 뒤 다음 오류에서 바로 다시 로그인)
                        stop = _report_block(gate, spec, reporter, attempt)
                        if stop:
                            return stop
                        continue
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
