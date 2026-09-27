"""모드별 예약 실행기

- SubprocessLauncher: ``python -m telegramBot.worker`` 프로세스 실행 (stdin으로 명세 전달)
- CeleryLauncher: ``reservation_task`` 를 ``task_id=reservation_id`` 로 발행
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from typing import Callable, Optional, Protocol

from .crypto import CredentialVault

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


class SubprocessLauncher:
    name = "subprocess"

    def __init__(
        self,
        on_exit: Optional[ExitCallback] = None,
        command: Optional[list[str]] = None,
    ):
        # 프로세스가 종료되면 (reservation_id, returncode)로 호출 (별도 스레드에서)
        self.on_exit = on_exit
        self.command = command or [sys.executable, "-m", "telegramBot.worker"]

    def launch(self, spec: dict) -> str:
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, f"worker_{spec['reservation_id'][:8]}.log")
        log_file = open(log_path, "a")

        process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=log_file,
            text=True,
            cwd=SRC_DIR,
            start_new_session=True,  # 취소 시 프로세스 그룹 전체 종료
        )
        try:
            process.stdin.write(json.dumps(spec))
            process.stdin.close()
        except Exception:
            process.kill()
            log_file.close()
            raise

        reservation_id = spec["reservation_id"]

        def _wait():
            returncode = process.wait()
            log_file.close()
            if self.on_exit:
                try:
                    self.on_exit(reservation_id, returncode)
                except Exception as e:
                    logger.error(f"on_exit callback failed: {e}")

        threading.Thread(target=_wait, daemon=True).start()
        return str(process.pid)

    def cancel(self, runner_ref: str) -> None:
        try:
            os.killpg(os.getpgid(int(runner_ref)), signal.SIGTERM)
        except ProcessLookupError:
            logger.info(f"Process {runner_ref} already terminated")


class CeleryLauncher:
    name = "celery"

    # 취소 표시 보관 시간 (재전달된 태스크도 시작하지 않도록 충분히 길게)
    CANCEL_TTL_SECONDS = 7 * 24 * 3600

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
                key = f"reservation_task:{runner_ref}"
                pipe = self.redis_client.pipeline()
                pipe.hset(key, "status", "cancelled")
                pipe.expire(key, self.CANCEL_TTL_SECONDS)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Failed to mark reservation cancelled in Redis: {e}")
        # 아직 대기열에 있는 태스크는 워커가 받는 즉시 버림
        self.celery_app.control.revoke(runner_ref, terminate=self.terminate)


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
