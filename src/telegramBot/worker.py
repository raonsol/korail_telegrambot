"""Subprocess 모드 예약 워커

웹 서버(``SubprocessLauncher``)는 이 모듈을 미리 import해 둔 forkserver에서
워커 프로세스를 복제하고 ``run_process(spec, log_path)``를 실행합니다.
라이브러리를 예약마다 다시 불러오지 않아 메모리(예약당 22MB → 약 6MB)와 시작 시간이 줄어듭니다.
예약 명세는 명령행 인자가 아닌 forkserver 소켓/파이프로 전달됩니다(``ps``로 비밀번호가 노출되지 않음).

수동 실행 시에는 stdin(JSON)으로 명세를 받습니다.

    echo '{"reservation_id": ..., "korail_pw": ...}' | python -m telegramBot.worker
"""

import json
import logging
import os
import signal
import sys
from contextlib import suppress
from datetime import datetime

from core.egress import FileGate, NullGate, file_gate_path
from core.runner import (
    build_reporter,
    result_file,
    run_reservation,
    write_result_file,
)

logs_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = (
    "reservation_id",
    "callback_url",
    "callback_token",
    "korail_id",
    "korail_pw",
    "dep_date",
    "src_station",
    "dst_station",
    "dep_time",
    "max_dep_time",
    "train_type",
    "seat_type",
)


def read_spec(stream=None) -> dict:
    stream = stream or sys.stdin
    spec = json.loads(stream.read())
    missing = [field for field in REQUIRED_FIELDS if field not in spec]
    if missing:
        raise ValueError(f"Missing fields: {missing}")
    return spec


def _handle_termination(signum, frame):
    # 취소는 웹 서버가 이미 상태를 기록했으므로 별도 보고 없이 종료
    logger.info(f"Received termination signal {signum}")
    sys.exit(0)


def _setup_logging(handlers, force: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=force,
    )
    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)


def build_gate(spec: dict, state_dir: str):
    """이 서버의 다른 워커와 공유하는 출구 상태 (출구별 파일)"""
    egress_id = spec.get("egress_id")
    if not egress_id:
        return NullGate()
    return FileGate(
        file_gate_path(state_dir, egress_id), rpm=int(spec.get("egress_rpm") or 0)
    )


def _run(spec: dict, result_path: str | None = None, state_dir: str = logs_dir) -> int:
    reporter = build_reporter(spec)

    def save_result(train_info: str, attempts: int, waiting: bool = False) -> None:
        # 성공 보고 전에 결과를 남겨 둠: 보고가 끝내 전달되지 않거나 이 프로세스가
        # 종료돼도(웹 서버 재시작) 웹 서버가 이 파일로 성공을 복구함
        if result_path:
            try:
                write_result_file(result_path, train_info, attempts, waiting)
            except OSError as e:
                logger.error(f"Failed to save reservation result: {e}")

    try:
        result = run_reservation(
            spec, reporter, on_success=save_result, gate=build_gate(spec, state_dir)
        )
        logger.info(f"Reservation {spec['reservation_id']} finished: {result}")
        if result.get("reported") and result_path:
            with suppress(OSError):
                os.remove(result_path)
        return 0
    except Exception as e:
        logger.exception("Reservation worker crashed")
        reporter.send("error", message=f"예약 중 오류 발생: {e}")
        return 1


def run_process(spec: dict, log_path: str) -> None:
    """forkserver에서 복제된 워커 프로세스의 진입점 (종료 코드는 main()과 같음)"""
    # print/예외 출력까지 예약별 로그 파일로
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    _setup_logging([logging.StreamHandler(sys.stderr)], force=True)

    missing = [field for field in REQUIRED_FIELDS if field not in spec]
    if missing:
        logger.error(f"Invalid reservation spec: Missing fields: {missing}")
        sys.exit(2)
    results_dir = os.path.dirname(os.path.abspath(log_path))
    sys.exit(_run(spec, result_file(results_dir, spec["reservation_id"]), results_dir))


def main() -> int:
    _setup_logging(
        [
            logging.FileHandler(
                os.path.join(
                    logs_dir, f'worker_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
                )
            ),
            logging.StreamHandler(),
        ]
    )

    try:
        spec = read_spec()
    except Exception as e:
        logger.error(f"Invalid reservation spec: {e}")
        return 2
    return _run(spec)


if __name__ == "__main__":
    sys.exit(main())
