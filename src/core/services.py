"""서비스 조립 (app.py와 테스트에서 사용)"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .auth import AuthService, KorailLogin, default_korail_login
from .crypto import CredentialVault
from .db import Database
from .launchers import Launcher, create_launcher
from .notifier import Notifier, SSEBroker, WebPushChannel
from .reservations import ReservationService
from .users import UserService

logger = logging.getLogger(__name__)

HOUSEKEEPING_INTERVAL_SECONDS = 600


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

    launcher = launcher or create_launcher(
        use_celery,
        settings.redis_url,
        settings.redis_db,
        vault,
        celery_pool=settings.celery_pool,
    )
    reservations = ReservationService(
        db,
        launcher,
        notifier,
        callback_url=settings.callback_url,
        max_active_total=settings.max_concurrent_reservations,
        max_active_per_user=settings.max_reservations_per_user,
        retention_days=settings.reservation_retention_days,
        max_duration=settings.reservation_timeout,
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
        push=push,
    )


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


async def housekeeping_loop(
    services: Services, interval: int = HOUSEKEEPING_INTERVAL_SECONDS
) -> None:
    while True:
        await run_housekeeping(services)
        await asyncio.sleep(interval)
