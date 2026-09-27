"""
Celery tasks for background reservation processing
"""

import logging
import os
import threading
import time

import redis
from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_shutting_down

from config import celery_settings, web_settings
from core.crypto import CredentialVault
from core.runner import MAX_DURATION_MESSAGE, build_reporter, run_reservation
from core.task_state import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_TTL_SECONDS,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    STOP_STATUSES,
    heartbeat_key,
    state_key,
)

logger = logging.getLogger(__name__)

RESERVATION_TIMEOUT = celery_settings.reservation_timeout
# 예약 상태 키 보관 시간. 브로커 재전달(visibility_timeout)보다 길게 유지
STATE_TTL_SECONDS = RESERVATION_TIMEOUT * 2

WORKER_RESTART_MESSAGE = (
    "예약 워커가 다시 시작되어 예약이 중단되었습니다. 예약을 다시 시작해 주세요."
)

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
    return state_key(task_id)


# 워커 종료(배포·docker stop)가 시작되면 실행 중인 예약을 다음 시도 전에 멈추고
# 웹 서버에 바로 알린다. threads 풀은 태스크가 이 프로세스의 스레드라 플래그가 그대로 보인다
# (prefork 자식 프로세스에는 전달되지 않음 → heartbeat 만료로 웹 서버가 감지)
_shutting_down = threading.Event()


@worker_shutting_down.connect
def _on_worker_shutting_down(**_):
    logger.info("Worker shutting down: stopping running reservations")
    _shutting_down.set()


class Heartbeat:
    """이 워커 프로세스에서 실행 중인 예약의 heartbeat 키를 주기적으로 갱신

    프로세스가 죽으면(크래시·OOM·SIGKILL) 갱신이 멈춰 키가 만료되고,
    웹 서버(ReservationService.detect_lost_workers)가 이를 보고 예약을 오류 처리한다.
    """

    def __init__(self, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        self.interval = interval
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self._client = None
        self._thread_pid = None

    def add(self, client, reservation_id: str) -> None:
        with self._lock:
            self._ids.add(reservation_id)
            self._client = client
            if self._thread_pid != os.getpid():  # prefork 자식은 각자 스레드
                self._thread_pid = os.getpid()
                threading.Thread(
                    target=self._run, name="reservation-heartbeat", daemon=True
                ).start()

    def remove(self, client, reservation_id: str) -> None:
        with self._lock:
            self._ids.discard(reservation_id)
        try:
            client.delete(heartbeat_key(reservation_id))
        except Exception as e:
            logger.warning(f"Failed to remove heartbeat: {e}")

    def beat(self) -> None:
        with self._lock:
            ids, client = list(self._ids), self._client
        if not ids or client is None:
            return
        try:
            pipe = client.pipeline(transaction=False)
            for reservation_id in ids:
                pipe.set(heartbeat_key(reservation_id), "1", ex=HEARTBEAT_TTL_SECONDS)
            pipe.execute()
        except Exception as e:
            logger.warning(f"Failed to refresh heartbeats: {e}")

    def _run(self) -> None:
        while True:
            time.sleep(self.interval)
            self.beat()


heartbeat = Heartbeat()


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
    reservation_id = self.request.id
    key = reservation_key(reservation_id)
    redis_client = None
    try:
        redis_client = get_redis()
        status = redis_client.hget(key, "status")
        if status in STOP_STATUSES:
            logger.info(f"Reservation already {status} for key: {key}, skipping")
            return {"status": f"already_{status}"}
        pipe = redis_client.pipeline()
        # 그 사이 취소됐다면 cancelled를 덮어쓰지 않음 (should_stop이 바로 멈춤)
        pipe.hsetnx(key, "status", STATUS_RUNNING)
        pipe.expire(key, STATE_TTL_SECONDS)
        # 시작 표시와 heartbeat를 함께 기록 (웹 서버가 "시작됐는데 heartbeat 없음"으로 오판하지 않도록)
        pipe.set(heartbeat_key(reservation_id), "1", ex=HEARTBEAT_TTL_SECONDS)
        pipe.execute()
        heartbeat.add(redis_client, reservation_id)
    except Exception as redis_error:
        logger.warning(
            f"Redis unavailable, continuing without state check: {redis_error}"
        )
        redis_client = None

    try:
        return _run_task(spec, reporter, redis_client, key)
    finally:
        if redis_client is not None:
            # 정상 종료 후에는 heartbeat를 지움. 결과 보고가 실패해 웹 서버에 예약이
            # 진행 중으로 남아 있으면(배포 중 웹 서버 재시작 등) 다음 확인 때 바로 정리됨
            heartbeat.remove(redis_client, reservation_id)


def _run_task(spec: dict, reporter, redis_client, key: str) -> dict:
    def should_stop() -> bool:
        if _shutting_down.is_set():
            return True
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
            return redis_client.hget(key, "status") == STATUS_COMPLETED
        except Exception:
            return False

    def mark_completed() -> None:
        if redis_client:
            try:
                redis_client.hset(key, "status", STATUS_COMPLETED)
            except Exception as e:
                logger.warning(f"Failed to update Redis status: {e}")

    password = _resolve_password(spec)
    if not password:
        reporter.send("error", message="예약 정보를 복호화할 수 없습니다.")
        return {"status": "error", "message": "missing credentials"}

    try:
        result = run_reservation(
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

    if result.get("status") == "stopped" and _shutting_down.is_set():
        # 취소된 예약이었다면 웹 서버가 이 보고를 무시함 (applied: false)
        reporter.send("error", message=WORKER_RESTART_MESSAGE)
        return {**result, "status": "interrupted"}
    return result


if __name__ == "__main__":
    app.start()
