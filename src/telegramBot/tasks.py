"""
Celery tasks for background reservation processing
"""

import logging

import redis
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded

from config import celery_settings, web_settings
from core.crypto import CredentialVault
from core.runner import MAX_ATTEMPTS_MESSAGE, build_reporter, run_reservation

logger = logging.getLogger(__name__)

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
    task_time_limit=celery_settings.reservation_timeout,
    task_soft_time_limit=celery_settings.reservation_timeout - 60,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=50,
)


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


@app.task(bind=True)
def reservation_task(self, spec: dict):
    """
    Background task for KTX reservation

    Args:
        spec: 예약 명세 (task_id == reservation_id)
    """
    reporter = build_reporter(spec)

    # Use Celery task ID as unique key to prevent duplicate task execution
    # (task_acks_late로 재전달되는 경우 이미 성공한 예약은 다시 시도하지 않음)
    key = reservation_key(self.request.id)
    redis_client = None
    try:
        redis_client = redis.Redis.from_url(
            web_settings.redis_url, db=web_settings.redis_db, decode_responses=True
        )
        if redis_client.hget(key, "status") == "completed":
            logger.info(f"Reservation already completed for key: {key}, skipping")
            return {"status": "already_completed"}
        redis_client.hset(key, "status", "running")
    except Exception as redis_error:
        logger.warning(
            f"Redis unavailable, continuing without state check: {redis_error}"
        )
        redis_client = None

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
            {**spec, "korail_pw": password},
            reporter,
            should_stop=is_completed,
            on_success=mark_completed,
        )
    except SoftTimeLimitExceeded:
        if is_completed():
            return {"status": "success", "message": "completed before timeout"}
        reporter.send("failed", message=MAX_ATTEMPTS_MESSAGE)
        return {"status": "failed", "message": "soft time limit exceeded"}
    except Exception as e:
        logger.exception(f"Reservation task error: {spec.get('reservation_id')}")
        reporter.send("error", message=f"예약 중 오류 발생: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    app.start()
