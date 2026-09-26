"""Subprocess 모드 예약 워커

예약 명세는 명령행 인자가 아닌 **stdin(JSON)** 으로 전달받습니다.
(argv로 넘기면 ``ps``로 코레일 비밀번호가 노출되기 때문)

    echo '{"reservation_id": ..., "korail_pw": ...}' | python -m telegramBot.worker
"""

import json
import logging
import os
import signal
import sys
from datetime import datetime

from core.runner import build_reporter, run_reservation

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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(
                os.path.join(
                    logs_dir, f'worker_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
                )
            ),
            logging.StreamHandler(),
        ],
    )
    signal.signal(signal.SIGTERM, _handle_termination)
    signal.signal(signal.SIGINT, _handle_termination)

    try:
        spec = read_spec()
    except Exception as e:
        logger.error(f"Invalid reservation spec: {e}")
        return 2

    reporter = build_reporter(spec)
    try:
        result = run_reservation(spec, reporter)
        logger.info(f"Reservation {spec['reservation_id']} finished: {result}")
        return 0
    except Exception as e:
        logger.exception("Reservation worker crashed")
        reporter.send("error", message=f"예약 중 오류 발생: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
