"""
Celery tasks for background reservation processing
"""

import logging
import threading

import redis
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from config import celery_settings, web_settings
from core.crypto import CredentialVault
from core.runner import MAX_DURATION_MESSAGE, build_reporter, run_reservation

logger = logging.getLogger(__name__)

RESERVATION_TIMEOUT = celery_settings.reservation_timeout
# 예약 상태 키 보관 시간. 브로커 재전달(visibility_timeout)보다 길게 유지
STATE_TTL_SECONDS = RESERVATION_TIMEOUT * 2
# Redis 상태가 이 값이면 태스크를 시작하지 않거나 즉시 멈춤
# cancelled는 웹 서버(CeleryLauncher.cancel)가 기록 (threads 풀은 강제 종료가 불가능)
STOP_STATUSES = ("completed", "cancelled")

# Initialize Celery app
app = Celery("korail_reservations")
app.conf.update(
    broker_url=celery_settings.celery_broker,
    result_backend=celery_settings.celery_result_backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
    # 기본 풀은 threads (예약 태스크는 대부분 대기 시간이라 스레드로 충분하고 메모리가 작음)
    # CLI의 --pool 기본값(prefork)이 이 설정보다 우선하므로 실행 명령에도 --pool을 지정
    worker_pool=celery_settings.celery_pool,
    worker_concurrency=celery_settings.worker_concurrency,
    # 최대 실행 시간은 공통 루프(spec["max_duration"])가 지킨다.
    # 아래 시간 제한은 prefork 풀에서만 동작하는 안전장치 (threads 풀은 무시)
    task_time_limit=RESERVATION_TIMEOUT + 120,
    task_soft_time_limit=RESERVATION_TIMEOUT + 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # Redis 브로커는 ack되지 않은 태스크를 visibility_timeout(기본 1시간) 뒤 재전달한다.
    # 실행 중인 예약이 중복 실행되지 않도록 최대 실행 시간보다 길게 설정
    broker_transport_options={"visibility_timeout": STATE_TTL_SECONDS},
    worker_max_tasks_per_child=50,  # prefork 전용 (threads 풀은 무시)
)

_redis_client = None
_redis_lock = threading.Lock()


def get_redis() -> redis.Redis:
    """워커 프로세스에서 공유하는 Redis 클라이언트 (연결 풀은 스레드 안전)"""
    global _redis_client
    with _redis_lock:
        if _redis_client is None:
            _redis_client = redis.Redis.from_url(
                web_settings.redis_url,
                db=web_settings.redis_db,
                decode_responses=True,
            )
        return _redis_client


def reservation_key(task_id: str) -> str:
    return f"reservation_task:{task_id}"


def _resolve_password(spec: dict) -> str | None:
    """브로커에는 암호화된 비밀번호(korail_pw_enc)가 실리고 워커에서 복호화"""
    if spec.get("korail_pw"):
        return spec["korail_pw"]
    encrypted = spec.get("korail_pw_enc")
    if encrypted:
        return CredentialVault(web_settings.webapp_enc_key).decrypt(encrypted)
    return None


def _max_duration(spec: dict) -> int:
    """웹 서버가 정한 최대 실행 시간. 워커 설정(visibility_timeout 기준)보다 길 수 없음"""
    requested = spec.get("max_duration")
    if not requested:
        return RESERVATION_TIMEOUT
    return min(int(requested), RESERVATION_TIMEOUT)


@app.task(bind=True)
def reservation_task(self, spec: dict):
    """
    Background task for KTX reservation

    Args:
        spec: 예약 명세 (task_id == reservation_id)
    """
    reporter = build_reporter(spec)

    # Use Celery task ID as unique key to prevent duplicate task execution
    # (task_acks_late로 재전달되는 경우 이미 성공했거나 취소된 예약은 다시 시도하지 않음)
    key = reservation_key(self.request.id)
    redis_client = None
    try:
        redis_client = get_redis()
        status = redis_client.hget(key, "status")
        if status in STOP_STATUSES:
            logger.info(f"Reservation already {status} for key: {key}, skipping")
            return {"status": f"already_{status}"}
        # 그 사이 취소됐다면 cancelled를 덮어쓰지 않음 (should_stop이 바로 멈춤)
        redis_client.hsetnx(key, "status", "running")
        redis_client.expire(key, STATE_TTL_SECONDS)
    except Exception as redis_error:
        logger.warning(
            f"Redis unavailable, continuing without state check: {redis_error}"
        )
        redis_client = None

    def should_stop() -> bool:
        if not redis_client:
            return False
        try:
            return redis_client.hget(key, "status") in STOP_STATUSES
        except Exception:
            return False

    def is_completed() -> bool:
        if not redis_client:
            return False
        try:
            return redis_client.hget(key, "status") == "completed"
        except Exception:
            return False

    def mark_completed() -> None:
        if redis_client:
            try:
                redis_client.hset(key, "status", "completed")
            except Exception as e:
                logger.warning(f"Failed to update Redis status: {e}")

    password = _resolve_password(spec)
    if not password:
        reporter.send("error", message="예약 정보를 복호화할 수 없습니다.")
        return {"status": "error", "message": "missing credentials"}

    try:
        return run_reservation(
            {**spec, "korail_pw": password, "max_duration": _max_duration(spec)},
            reporter,
            should_stop=should_stop,
            on_success=mark_completed,
        )
    except SoftTimeLimitExceeded:
        if is_completed():
            return {"status": "success", "message": "completed before timeout"}
        reporter.send("failed", message=MAX_DURATION_MESSAGE)
        return {"status": "failed", "message": "soft time limit exceeded"}
    except Exception as e:
        logger.exception(f"Reservation task error: {spec.get('reservation_id')}")
        reporter.send("error", message=f"예약 중 오류 발생: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    app.start()
