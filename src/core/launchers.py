"""모드별 예약 실행기

- SubprocessLauncher: 워커 모듈을 미리 불러 둔 forkserver에서 예약마다 프로세스를 복제
- CeleryLauncher: ``reservation_task`` 를 ``task_id=reservation_id`` 로 발행
"""

import logging
import multiprocessing
import os
import threading
from multiprocessing.connection import wait as wait_ready
from typing import Callable, Optional, Protocol

from .crypto import CredentialVault
from .task_state import (
    CANCEL_TTL_SECONDS,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    heartbeat_key,
    state_key,
)

logger = logging.getLogger(__name__)

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS_DIR = os.path.abspath(os.path.join(SRC_DIR, "..", "logs"))

ExitCallback = Callable[[str, int], None]


class Launcher(Protocol):
    name: str

    def launch(self, spec: dict) -> str:
        """예약 실행 후 취소에 사용할 참조값(PID 또는 task_id) 반환"""
        ...

    def cancel(self, runner_ref: str) -> None: ...

    # 선택: 워커가 남긴 성공 결과 (보고가 전달되지 않은 경우 복구용)
    # def recover_success(self, reservation_id: str) -> Optional[dict]: ...


WorkerTarget = Callable[[dict, str], None]

# forkserver가 미리 import하는 워커 모듈 (복제된 워커들이 메모리를 공유)
WORKER_PRELOAD = ["telegramBot.worker"]


class SubprocessLauncher:
    """예약마다 워커 프로세스 1개 (multiprocessing forkserver)

    - 워커 모듈을 한 번만 불러 두고 복제하므로 예약당 메모리가 약 6MB (새 인터프리터는 22MB)
    - 명세는 forkserver 소켓/파이프로 전달 (argv에 비밀번호가 실리지 않음)
    - 워커는 daemon 프로세스라 웹 서버가 종료되면 같이 종료됨
      (중단된 예약은 다음 시작 때 ReservationService.abort_interrupted()가 정리)
    """

    name = "subprocess"

    def __init__(
        self,
        on_exit: Optional[ExitCallback] = None,
        target: Optional[WorkerTarget] = None,
    ):
        # 프로세스가 종료되면 (reservation_id, returncode)로 호출 (별도 스레드에서)
        self.on_exit = on_exit
        # 워커 진입점 target(spec, log_path). 기본값 telegramBot.worker.run_process
        self.target = target
        self._ctx = multiprocessing.get_context("forkserver")
        self._ctx.set_forkserver_preload(WORKER_PRELOAD)
        # Process.start()는 다른 자식의 종료 코드를 읽고(_cleanup), 종료 코드는 파이프에서
        # 한 번만 읽을 수 있다. 시작과 종료 코드 확인을 이 잠금으로 직렬화해
        # 두 스레드가 같은 파이프를 읽다가 코드가 255로 바뀌는 경쟁을 막는다
        self._lock = threading.Lock()
        self._processes: dict[int, multiprocessing.process.BaseProcess] = {}

    def _worker_target(self) -> WorkerTarget:
        if self.target is None:
            from telegramBot.worker import run_process

            self.target = run_process
        return self.target

    def launch(self, spec: dict) -> str:
        os.makedirs(LOGS_DIR, exist_ok=True)
        reservation_id = spec["reservation_id"]
        log_path = os.path.join(LOGS_DIR, f"worker_{reservation_id[:8]}.log")

        process = self._ctx.Process(
            target=self._worker_target(),
            args=(spec, log_path),
            name=f"reservation-{reservation_id[:8]}",
            daemon=True,
        )
        with self._lock:
            process.start()
            pid = process.pid
            self._processes[pid] = process

        threading.Thread(
            target=self._wait, args=(reservation_id, pid, process), daemon=True
        ).start()
        return str(pid)

    def _wait(self, reservation_id: str, pid: int, process) -> None:
        wait_ready([process.sentinel])  # 종료될 때까지 대기 (파이프는 읽지 않음)
        with self._lock:
            returncode = process.exitcode
            self._processes.pop(pid, None)
            process.close()
        if self.on_exit:
            try:
                self.on_exit(reservation_id, returncode)
            except Exception as e:
                logger.error(f"on_exit callback failed: {e}")

    def cancel(self, runner_ref: str) -> None:
        with self._lock:
            process = self._processes.get(int(runner_ref))
            if process is None:
                # 이미 끝났거나 이 서버가 띄운 워커가 아님 (재시작 전 PID는 재사용됐을 수 있음)
                logger.info(f"Process {runner_ref} is not running")
                return
            process.terminate()  # 해당 워커에만 SIGTERM (웹 서버와 같은 프로세스 그룹)

    def running_count(self) -> int:
        with self._lock:
            return len(self._processes)

    def recover_success(self, reservation_id: str) -> Optional[dict]:
        """워커가 남긴 성공 결과 파일 ({"train_info", "attempts"}). 읽으면 삭제"""
        from .runner import read_result_file, result_file

        path = result_file(LOGS_DIR, reservation_id)
        result = read_result_file(path)
        if result is not None:
            try:
                os.remove(path)
            except OSError:
                pass
        return result


class CeleryLauncher:
    name = "celery"

    def __init__(
        self,
        celery_app,
        redis_client=None,
        vault: CredentialVault = None,
        terminate: bool = False,
    ):
        self.celery_app = celery_app
        self.redis_client = redis_client
        self.vault = vault
        # prefork 풀만 실행 중인 태스크를 강제 종료할 수 있음
        # threads 풀은 kill을 지원하지 않으므로 Redis 취소 표시로 멈춤 (협조적 취소)
        self.terminate = terminate

    def launch(self, spec: dict) -> str:
        from telegramBot.tasks import reservation_task

        payload = dict(spec)
        # 브로커(Redis)에 평문 비밀번호가 남지 않도록 암호화 (키가 고정된 경우만 가능)
        if self.vault and self.vault.persistent:
            payload["korail_pw_enc"] = self.vault.encrypt(payload.pop("korail_pw"))
        else:
            logger.warning(
                "WEBAPP_ENC_KEY is not set; Korail password is sent to the broker in plaintext"
            )

        reservation_task.apply_async(
            kwargs={"spec": payload}, task_id=spec["reservation_id"]
        )
        return spec["reservation_id"]

    def cancel(self, runner_ref: str) -> None:
        # 실행 중인 태스크는 다음 시도 전에 이 표시를 보고 멈춤 (core/runner.py should_stop)
        if self.redis_client:
            try:
                key = state_key(runner_ref)
                pipe = self.redis_client.pipeline()
                pipe.hset(key, "status", STATUS_CANCELLED)
                pipe.expire(key, CANCEL_TTL_SECONDS)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Failed to mark reservation cancelled in Redis: {e}")
        # 아직 대기열에 있는 태스크는 워커가 받는 즉시 버림
        self.celery_app.control.revoke(runner_ref, terminate=self.terminate)

    def find_lost(self, runner_refs: list[str]) -> list[str]:
        """워커가 더 이상 실행하지 않는 태스크 (시작됐는데 heartbeat가 없음)

        - running: 워커가 종료됨 (재시작·크래시)
        - completed: 예약은 성공했지만 성공 보고가 전달되지 않음 → recover_success로 복구
        대기열에 있어 아직 시작하지 않은 태스크(상태 키 없음)나 Redis 없이 실행된
        태스크는 판단할 수 없으므로 제외한다 (30분 무응답 정리가 처리).
        """
        if not self.redis_client or not runner_refs:
            return []
        pipe = self.redis_client.pipeline(transaction=False)
        for ref in runner_refs:
            pipe.hget(state_key(ref), "status")
            pipe.exists(heartbeat_key(ref))
        values = pipe.execute()
        return [
            ref
            for ref, status, alive in zip(runner_refs, values[0::2], values[1::2])
            if status in (STATUS_RUNNING, STATUS_COMPLETED) and not alive
        ]

    def recover_success(self, reservation_id: str) -> Optional[dict]:
        """태스크가 Redis에 남긴 성공 결과 ({"train_info", "attempts"})"""
        if not self.redis_client:
            return None
        state = self.redis_client.hgetall(state_key(reservation_id))
        if state.get("status") != STATUS_COMPLETED or not state.get("train_info"):
            return None
        return {
            "train_info": state["train_info"],
            "attempts": int(state.get("attempts") or 0),
        }


def create_launcher(
    use_celery: bool,
    redis_url: str,
    redis_db: int = 0,
    vault: CredentialVault = None,
    celery_pool: str = "threads",
) -> Launcher:
    """설정에 따라 실행기 생성. Redis 연결 실패 시 subprocess 모드로 대체"""
    if use_celery:
        try:
            import redis

            redis_client = redis.Redis.from_url(
                redis_url, db=redis_db, decode_responses=True
            )
            redis_client.ping()
            from telegramBot.tasks import app as celery_app

            logger.info("Redis and Celery initialized successfully")
            return CeleryLauncher(
                celery_app, redis_client, vault, terminate=celery_pool == "prefork"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Redis/Celery, using subprocess: {e}")
    return SubprocessLauncher()
