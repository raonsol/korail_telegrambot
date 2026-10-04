"""서비스 조립 (app.py와 테스트에서 사용)"""

import asyncio
import functools
import logging
import os
from dataclasses import dataclass
from typing import Optional

from .auth import AuthService, KorailLogin, default_korail_login
from .crypto import CredentialVault
from .db import Database
from .egress import EgressPool, FileGate, MemoryGate, RedisGate, file_gate_path
from .launchers import (
    LOGS_DIR,
    CeleryLauncher,
    Launcher,
    SubprocessLauncher,
    create_launcher,
)
from .notifier import Notifier, SSEBroker, WebPushChannel
from .reservations import ReservationService
from .users import UserService

logger = logging.getLogger(__name__)

HOUSEKEEPING_INTERVAL_SECONDS = 600
# Celery 워커 heartbeat 만료(60초) 후 이 주기 안에 감지
LOST_WORKER_CHECK_SECONDS = 60


@dataclass
class Services:
    settings: object
    db: Database
    vault: CredentialVault
    users: UserService
    auth: AuthService
    notifier: Notifier
    sse: SSEBroker
    reservations: ReservationService
    egresses: EgressPool
    push: Optional[WebPushChannel] = None

    def init_storage(self) -> None:
        """테이블 생성 + ALLOW_LIST 시드"""
        self.db.create_all()
        self.users.seed_from_allow_list(self.settings.allow_list)


def build_services(
    settings,
    use_celery: bool = False,
    *,
    db: Optional[Database] = None,
    launcher: Optional[Launcher] = None,
    korail_login: KorailLogin = default_korail_login,
) -> Services:
    db = db or Database(settings.database_url)
    vault = CredentialVault(settings.webapp_enc_key)
    users = UserService(db)
    launcher = launcher or create_launcher(
        use_celery,
        settings.redis_url,
        settings.redis_db,
        vault,
        celery_pool=settings.celery_pool,
    )
    egresses = EgressPool.from_settings(
        settings, gate_factory=_gate_factory(launcher, settings.korail_egress_rpm)
    )
    logger.info(f"Korail egresses: {egresses.describe()}")
    if korail_login is default_korail_login:
        korail_login = functools.partial(default_korail_login, egresses=egresses)
    auth = AuthService(
        db,
        users,
        vault,
        admin_password=settings.admin_password,
        admin_korail_id=settings.admin_korail_id,
        admin_korail_pw=settings.admin_korail_pw,
        session_ttl_hours=settings.session_ttl_hours,
        max_failures=settings.login_max_failures,
        lock_minutes=settings.login_lock_minutes,
        korail_login=korail_login,
    )
    notifier = Notifier()
    sse = SSEBroker()
    notifier.add(sse)

    push = None
    if settings.push_enabled:
        push = WebPushChannel(
            db,
            settings.vapid_public_key,
            settings.vapid_private_key,
            settings.vapid_subject,
        )
        notifier.add(push)

    reservations = ReservationService(
        db,
        launcher,
        notifier,
        callback_url=settings.callback_url,
        max_active_total=settings.max_concurrent_reservations,
        max_active_per_user=settings.max_reservations_per_user,
        retention_days=settings.reservation_retention_days,
        max_duration=settings.reservation_timeout,
        egresses=egresses,
    )
    return Services(
        settings=settings,
        db=db,
        vault=vault,
        users=users,
        auth=auth,
        notifier=notifier,
        sse=sse,
        reservations=reservations,
        egresses=egresses,
        push=push,
    )


def _gate_factory(launcher: Launcher, rpm: int):
    """출구 상태를 워커와 같은 곳에 둠 (subprocess: 파일, Celery: Redis)"""
    if isinstance(launcher, CeleryLauncher) and launcher.redis_client is not None:
        return lambda egress_id: RedisGate(launcher.redis_client, egress_id, rpm)
    if isinstance(launcher, SubprocessLauncher):

        def file_gate(egress_id: str) -> FileGate:
            os.makedirs(LOGS_DIR, exist_ok=True)
            return FileGate(file_gate_path(LOGS_DIR, egress_id), rpm)

        return file_gate
    return lambda egress_id: MemoryGate(rpm)


async def run_housekeeping(services: Services) -> None:
    """응답 없는 예약 정리, 만료 세션 삭제, 보관 기간(30일) 지난 이력 삭제"""
    try:
        stale = await services.reservations.expire_stale()
        purged = services.reservations.purge_history()
        sessions = services.auth.purge_expired()
        if stale or purged or sessions:
            logger.info(
                f"Housekeeping: stale={stale}, purged_history={purged}, "
                f"expired_sessions={sessions}"
            )
    except Exception as e:
        logger.error(f"Housekeeping failed: {e}")


async def check_lost_workers(services: Services) -> None:
    try:
        await services.reservations.detect_lost_workers()
    except Exception as e:
        logger.error(f"Lost worker check failed: {e}")


async def housekeeping_loop(
    services: Services,
    interval: int = HOUSEKEEPING_INTERVAL_SECONDS,
    lost_worker_interval: int = LOST_WORKER_CHECK_SECONDS,
) -> None:
    """정리 작업은 interval마다, 워커 종료 감지(Celery)는 lost_worker_interval마다"""
    loop = asyncio.get_running_loop()
    last_housekeeping = None
    while True:
        now = loop.time()
        if last_housekeeping is None or now - last_housekeeping >= interval:
            await run_housekeeping(services)
            last_housekeeping = now
        await check_lost_workers(services)
        await asyncio.sleep(min(interval, lost_worker_interval))
