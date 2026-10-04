"""예약 서비스 (채널 무관)

텔레그램 봇과 웹 API가 모두 이 서비스를 통해 예약을 시작/취소/조회하고,
워커의 상태 보고(``/internal/events``)도 여기서 처리합니다.

- 예약 식별자는 서비스가 발급하는 ``reservation_id`` (PID/Celery task_id는 ``runner_ref``)
- 워커 콜백은 예약마다 발급하는 1회용 토큰으로 인증
- 종료된 예약은 ``retention_days``(기본 30일) 동안 이력으로 보관
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
import uuid
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy import delete, func, or_, select

from .crypto import new_token, sha256_hex
from .db import Database, utcnow
from .errors import LimitExceeded, NotAllowed, NotFound, ServiceError
from .launchers import Launcher, SubprocessLauncher
from .models import Reservation
from .notifier import Notifier, ReservationEvent
from .schemas import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    Owner,
    ReservationOut,
    ReservationRequest,
    ReservationStatus,
    WorkerEvent,
)

logger = logging.getLogger(__name__)

WORKER_STATUS_MAP = {
    "running": ReservationStatus.RUNNING,
    "progress": ReservationStatus.RUNNING,
    "success": ReservationStatus.SUCCESS,
    "failed": ReservationStatus.FAILED,
    "error": ReservationStatus.ERROR,
}


INTERRUPTED_MESSAGE = (
    "서버가 다시 시작되어 예약이 중단되었습니다. 예약을 다시 시작해 주세요."
)
WORKER_LOST_MESSAGE = (
    "예약 워커가 종료되어 예약이 중단되었습니다. 예약을 다시 시작해 주세요."
)


class ReservationService:
    def __init__(
        self,
        db: Database,
        launcher: Launcher,
        notifier: Notifier,
        callback_url: str,
        max_active_total: int = 10,
        max_active_per_user: int = 3,
        retention_days: int = 30,
        max_duration: Optional[int] = None,
        device_for: Optional[Callable[[str], Optional[dict]]] = None,
    ):
        self.db = db
        # 코레일 계정의 기기 신원 (UserService.korail_device) - 워커가 같은 기기로 로그인하도록 spec에 포함
        self.device_for = device_for
        self.launcher = launcher
        self.notifier = notifier
        self.callback_url = callback_url
        self.max_active_total = max_active_total
        self.max_active_per_user = max_active_per_user
        self.retention_days = retention_days
        # 워커가 예약을 시도하는 최대 시간(초). 워커 루프가 직접 확인 (core/runner.py)
        self.max_duration = max_duration
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._admission_lock = threading.Lock()

        if isinstance(launcher, SubprocessLauncher):
            launcher.on_exit = self._on_process_exit_threadsafe

    @property
    def runner_name(self) -> str:
        return self.launcher.name

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    # ------------------------------------------------------------------ 조회

    @staticmethod
    def _can_access(
        r: Reservation, owner: Optional[Owner], chat_id: Optional[int]
    ) -> bool:
        if owner and (owner.is_admin or r.owner_id == owner.user_id):
            return True
        return chat_id is not None and r.chat_id == chat_id

    def list(
        self,
        owner: Optional[Owner] = None,
        *,
        chat_id: Optional[int] = None,
        active: Optional[bool] = None,
        scope_all: bool = False,
        limit: int = 200,
    ) -> list[ReservationOut]:
        """예약 목록 (최신순)

        Args:
            owner: 해당 소유자의 예약
            chat_id: 해당 텔레그램 채팅에서 시작한 예약 (owner와 OR 조건)
            active: True면 진행 중, False면 종료된 예약만
            scope_all: 관리자 전용 - 모든 사용자의 예약
        """
        stmt = select(Reservation)
        if scope_all:
            if not (owner and owner.is_admin):
                raise NotAllowed("관리자만 전체 예약을 조회할 수 있습니다.")
        else:
            conditions = []
            if owner:
                conditions.append(Reservation.owner_id == owner.user_id)
            if chat_id is not None:
                conditions.append(Reservation.chat_id == chat_id)
            if not conditions:
                return []
            stmt = stmt.where(or_(*conditions))
        if active is True:
            stmt = stmt.where(Reservation.status.in_(ACTIVE_STATUSES))
        elif active is False:
            stmt = stmt.where(Reservation.status.in_(TERMINAL_STATUSES))
        stmt = stmt.order_by(Reservation.created_at.desc()).limit(limit)
        with self.db.session() as s:
            return [ReservationOut.from_model(r) for r in s.scalars(stmt)]

    def get(
        self,
        reservation_id: str,
        owner: Optional[Owner] = None,
        chat_id: Optional[int] = None,
    ) -> ReservationOut:
        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            if not r or not self._can_access(r, owner, chat_id):
                raise NotFound("예약을 찾을 수 없습니다.")
            return ReservationOut.from_model(r)

    def count_active(self, owner_id: Optional[str] = None) -> int:
        stmt = select(func.count()).where(Reservation.status.in_(ACTIVE_STATUSES))
        if owner_id:
            stmt = stmt.where(Reservation.owner_id == owner_id)
        with self.db.session() as s:
            return s.scalar(stmt) or 0

    # ------------------------------------------------------------------ 시작/취소

    async def start(
        self,
        owner: Owner,
        request: ReservationRequest,
        korail_id: str,
        korail_pw: str,
        origin: str,
        chat_id: Optional[int] = None,
    ) -> ReservationOut:
        if self._loop is None:
            self.bind_loop()

        # DB 기록과 워커 프로세스 시작/Celery 발행은 블로킹 작업이라 스레드에서 수행
        # (이벤트 루프에서 직접 하면 동시 요청이 몰릴 때 다른 요청까지 멈춤)
        out = await asyncio.to_thread(
            self._start_blocking, owner, request, korail_id, korail_pw, origin, chat_id
        )
        await self.notifier.notify(
            ReservationEvent(out, previous_status="", source=origin, chat_id=chat_id)
        )
        return out

    def _start_blocking(
        self,
        owner: Owner,
        request: ReservationRequest,
        korail_id: str,
        korail_pw: str,
        origin: str,
        chat_id: Optional[int],
    ) -> ReservationOut:
        reservation_id = uuid.uuid4().hex
        token = new_token()

        # 한도 확인과 기록 사이에 다른 요청이 끼어들지 않도록 묶음
        # (웹 서버는 단일 프로세스로 실행되므로 프로세스 내 잠금으로 충분)
        with self._admission_lock:
            if self.count_active() >= self.max_active_total:
                raise LimitExceeded(
                    "현재 진행 중인 예약이 너무 많습니다. 잠시 후 다시 시도해주세요."
                )
            if (
                not owner.is_admin
                and self.count_active(owner.user_id) >= self.max_active_per_user
            ):
                raise LimitExceeded(
                    f"동시에 진행할 수 있는 예약은 최대 {self.max_active_per_user}개입니다."
                )

            now = utcnow()
            with self.db.session() as s:
                s.add(
                    Reservation(
                        id=reservation_id,
                        owner_id=owner.user_id,
                        origin=origin,
                        chat_id=chat_id,
                        korail_id=korail_id,
                        dep_date=request.dep_date_compact,
                        src_station=request.src_station,
                        dst_station=request.dst_station,
                        dep_time=request.dep_time,
                        max_dep_time=request.max_dep_time,
                        train_type=request.train_type,
                        seat_type=request.seat_type,
                        status=ReservationStatus.QUEUED.value,
                        runner=self.launcher.name,
                        callback_token_hash=sha256_hex(token),
                        attempts=0,
                        created_at=now,
                        updated_at=now,
                    )
                )

        spec = {
            "reservation_id": reservation_id,
            "callback_url": self.callback_url,
            "callback_token": token,
            "korail_id": korail_id,
            "korail_pw": korail_pw,
            "dep_date": request.dep_date_compact,
            "src_station": request.src_station,
            "dst_station": request.dst_station,
            "dep_time": request.dep_time,
            "max_dep_time": request.max_dep_time,
            "train_type": request.train_type,
            "seat_type": request.seat_type,
        }
        if self.max_duration:
            spec["max_duration"] = self.max_duration
        try:
            if self.device_for:
                spec["korail_device"] = self.device_for(korail_id)
            runner_ref = self.launcher.launch(spec)
        except Exception as e:
            logger.exception("Failed to launch reservation")
            self._finish(reservation_id, ReservationStatus.ERROR, error=str(e))
            raise ServiceError(
                "예약 시작 중 오류가 발생했습니다.", code="LAUNCH_FAILED"
            )

        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            r.runner_ref = runner_ref
            out = ReservationOut.from_model(r)

        logger.info(
            f"Started reservation {reservation_id} ({self.launcher.name}:{runner_ref}) "
            f"for {owner.user_id} via {origin}"
        )
        return out

    async def cancel(
        self,
        reservation_id: str,
        *,
        owner: Optional[Owner] = None,
        chat_id: Optional[int] = None,
        source: str,
    ) -> ReservationOut:
        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            if not r or not self._can_access(r, owner, chat_id):
                raise NotFound("예약을 찾을 수 없습니다.")
            if r.status not in ACTIVE_STATUSES:
                return ReservationOut.from_model(r)
            previous = r.status
            runner_ref = r.runner_ref
            r.status = ReservationStatus.CANCELLED.value
            r.updated_at = r.finished_at = utcnow()
            out = ReservationOut.from_model(r)
            reservation_chat = r.chat_id

        if runner_ref:
            try:
                self.launcher.cancel(runner_ref)
            except Exception as e:
                logger.error(f"Failed to stop runner {runner_ref}: {e}")

        await self.notifier.notify(
            ReservationEvent(out, previous, source=source, chat_id=reservation_chat)
        )
        return out

    async def cancel_many(
        self,
        *,
        owner: Optional[Owner] = None,
        chat_id: Optional[int] = None,
        scope_all: bool = False,
        source: str,
    ) -> list[ReservationOut]:
        targets = self.list(
            owner, chat_id=chat_id, active=True, scope_all=scope_all, limit=1000
        )
        cancelled = []
        for r in targets:
            cancelled.append(
                await self.cancel(
                    r.id,
                    owner=Owner(user_id="*", is_admin=True) if scope_all else owner,
                    chat_id=chat_id,
                    source=source,
                )
            )
        return cancelled

    # ------------------------------------------------------------------ 워커 보고

    async def handle_worker_event(self, event: WorkerEvent) -> bool:
        """워커 콜백 처리. 반영했으면 True, 무시했으면(이미 종료) False"""
        new_status = WORKER_STATUS_MAP[event.status]
        with self.db.session() as s:
            r = s.get(Reservation, event.reservation_id)
            if not r:
                raise NotFound("예약을 찾을 수 없습니다.")
            if not hmac.compare_digest(r.callback_token_hash, sha256_hex(event.token)):
                raise NotAllowed("잘못된 콜백 토큰입니다.")
            if r.status in TERMINAL_STATUSES and not (
                # 서버가 먼저 포기(재시작·워커 종료·무응답)했지만 워커가 실제로 표를 잡은 경우
                new_status == ReservationStatus.SUCCESS
                and r.status == ReservationStatus.ERROR.value
            ):
                return False

            previous = r.status
            r.status = new_status.value
            r.updated_at = utcnow()
            if event.attempts is not None:
                r.attempts = max(r.attempts or 0, event.attempts)
            if new_status == ReservationStatus.SUCCESS:
                r.result_text = event.train_info or event.message
                r.error = None
                r.finished_at = r.updated_at
            elif new_status in (ReservationStatus.FAILED, ReservationStatus.ERROR):
                r.error = event.message
                r.finished_at = r.updated_at
            out = ReservationOut.from_model(r)
            chat_id = r.chat_id

        await self.notifier.notify(
            ReservationEvent(out, previous, source="worker", chat_id=chat_id)
        )
        return True

    async def handle_process_exit(self, reservation_id: str, returncode: int) -> None:
        """subprocess가 결과 보고 없이 종료된 경우 (남긴 성공 결과가 있으면 성공으로)"""
        await self._close_unreported(
            reservation_id,
            f"예약 프로세스가 비정상 종료되었습니다. (code {returncode})",
        )

    def _on_process_exit_threadsafe(self, reservation_id: str, returncode: int) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(
            self.handle_process_exit(reservation_id, returncode), self._loop
        )

    async def _close_unreported(self, reservation_id: str, message: str) -> bool:
        """보고 없이 멈춘 예약 정리: 워커가 남긴 성공 결과가 있으면 성공, 없으면 오류

        워커는 성공 보고 전에 결과를 기록한다(subprocess: 결과 파일, Celery: Redis).
        보고가 전달되지 않았어도 표는 잡혔으므로 오류로 알리면 안 된다.
        """
        recover = getattr(self.launcher, "recover_success", None)
        if recover is not None:
            try:
                result = await asyncio.to_thread(recover, reservation_id)
            except Exception as e:
                logger.warning(f"Failed to recover result of {reservation_id}: {e}")
                result = None
            if result:
                return await self._record_success(
                    reservation_id, result["train_info"], result.get("attempts")
                )
        return await self._fail_if_active(reservation_id, message)

    async def _record_success(
        self, reservation_id: str, train_info: str, attempts: Optional[int]
    ) -> bool:
        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            if not r or r.status not in (
                *ACTIVE_STATUSES,
                ReservationStatus.ERROR.value,
            ):
                return False
            previous = r.status
            r.status = ReservationStatus.SUCCESS.value
            r.result_text = train_info
            r.error = None
            if attempts is not None:
                r.attempts = max(r.attempts or 0, attempts)
            r.updated_at = r.finished_at = utcnow()
            out = ReservationOut.from_model(r)
            chat_id = r.chat_id
        logger.warning(f"Recovered unreported success of {reservation_id}")
        await self.notifier.notify(
            ReservationEvent(out, previous, source="system", chat_id=chat_id)
        )
        return True

    async def _fail_if_active(self, reservation_id: str, message: str) -> bool:
        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            if not r or r.status not in ACTIVE_STATUSES:
                return False
            previous = r.status
            r.status = ReservationStatus.ERROR.value
            r.error = message
            r.updated_at = r.finished_at = utcnow()
            out = ReservationOut.from_model(r)
            chat_id = r.chat_id
        await self.notifier.notify(
            ReservationEvent(out, previous, source="system", chat_id=chat_id)
        )
        return True

    def _finish(self, reservation_id: str, status: ReservationStatus, error: str):
        with self.db.session() as s:
            r = s.get(Reservation, reservation_id)
            if r:
                r.status = status.value
                r.error = error
                r.updated_at = r.finished_at = utcnow()

    # ------------------------------------------------------------------ 정리 작업

    async def expire_stale(
        self, running_idle_minutes: int = 30, queued_hours: int = 24
    ) -> int:
        """응답이 끊긴 예약을 오류 처리 (워커 재시작 등으로 보고가 유실된 경우)"""
        now = utcnow()
        with self.db.session() as s:
            stale = list(
                s.scalars(
                    select(Reservation).where(
                        or_(
                            (Reservation.status == ReservationStatus.RUNNING.value)
                            & (
                                Reservation.updated_at
                                < now - timedelta(minutes=running_idle_minutes)
                            ),
                            (Reservation.status == ReservationStatus.QUEUED.value)
                            & (
                                Reservation.created_at
                                < now - timedelta(hours=queued_hours)
                            ),
                        )
                    )
                )
            )
        count = 0
        for r in stale:
            # 결과 복구가 먼저 (Celery 취소 표시가 완료 상태를 덮어쓰기 전에)
            if await self._close_unreported(
                r.id, "워커 응답이 없어 예약이 중단되었습니다."
            ):
                count += 1
            if r.runner == self.launcher.name and r.runner_ref:
                try:
                    self.launcher.cancel(r.runner_ref)
                except Exception:
                    pass
        return count

    async def abort_interrupted(self) -> int:
        """서버 재시작으로 멈춘 subprocess 예약을 오류 처리 (웹 서버 시작 시 1회)

        subprocess 워커는 웹 서버 프로세스와 함께 종료되므로(daemon), 시작 시점에
        진행 중으로 남아 있는 subprocess 예약은 더 이상 실행되지 않는다.
        혹시 살아남은 워커(웹 서버가 강제 종료된 경우)는 다음 보고가 거부돼 스스로 멈춘다.
        """
        with self.db.session() as s:
            ids = list(
                s.scalars(
                    select(Reservation.id).where(
                        Reservation.runner == SubprocessLauncher.name,
                        Reservation.status.in_(ACTIVE_STATUSES),
                    )
                )
            )
        count = 0
        for reservation_id in ids:
            if await self._close_unreported(reservation_id, INTERRUPTED_MESSAGE):
                count += 1
        if count:
            logger.warning(f"Marked {count} reservations interrupted by restart")
        return count

    async def detect_lost_workers(self) -> int:
        """워커가 종료돼(재시작·크래시) 멈춘 예약을 오류 처리 (Celery 모드, 1분마다)

        워커는 실행 중인 예약의 heartbeat를 Redis에 갱신한다(telegramBot.tasks.Heartbeat).
        정상 종료 시에는 워커가 직접 보고하므로, 여기서는 그 보고가 실패했거나
        워커가 강제 종료된 경우를 잡는다. subprocess 모드는 해당 없음(abort_interrupted).
        """
        find_lost = getattr(self.launcher, "find_lost", None)
        if find_lost is None:
            return 0
        with self.db.session() as s:
            rows = s.execute(
                select(Reservation.runner_ref, Reservation.id).where(
                    Reservation.runner == self.launcher.name,
                    Reservation.status.in_(ACTIVE_STATUSES),
                    Reservation.runner_ref.is_not(None),
                )
            ).all()
        if not rows:
            return 0
        by_ref = dict(rows)
        lost = await asyncio.to_thread(find_lost, list(by_ref))
        count = 0
        for ref in lost:
            # 성공했지만 보고가 전달되지 않은 태스크는 결과를 복구 (취소 표시보다 먼저)
            if await self._close_unreported(by_ref[ref], WORKER_LOST_MESSAGE):
                count += 1
            try:
                # 브로커가 태스크를 다시 전달해도 시작하지 않도록 취소 표시
                await asyncio.to_thread(self.launcher.cancel, ref)
            except Exception as e:
                logger.warning(f"Failed to mark lost task {ref} cancelled: {e}")
        if count:
            logger.warning(f"Marked {count} reservations whose worker stopped")
        return count

    def purge_history(self) -> int:
        """보관 기간이 지난 종료 예약 삭제"""
        cutoff = utcnow() - timedelta(days=self.retention_days)
        with self.db.session() as s:
            result = s.execute(
                delete(Reservation).where(
                    Reservation.status.in_(TERMINAL_STATUSES),
                    func.coalesce(Reservation.finished_at, Reservation.updated_at)
                    < cutoff,
                )
            )
            return result.rowcount or 0
