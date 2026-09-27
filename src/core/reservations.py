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
from typing import Optional

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
    ):
        self.db = db
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

        # DB 기록과 프로세스 실행(Popen)/Celery 발행은 블로킹 작업이라 스레드에서 수행
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
            if r.status in TERMINAL_STATUSES:
                return False

            previous = r.status
            r.status = new_status.value
            r.updated_at = utcnow()
            if event.attempts is not None:
                r.attempts = max(r.attempts or 0, event.attempts)
            if new_status == ReservationStatus.SUCCESS:
                r.result_text = event.train_info or event.message
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
        """subprocess가 결과 보고 없이 종료된 경우 오류로 기록"""
        await self._fail_if_active(
            reservation_id,
            f"예약 프로세스가 비정상 종료되었습니다. (code {returncode})",
        )

    def _on_process_exit_threadsafe(self, reservation_id: str, returncode: int) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(
            self.handle_process_exit(reservation_id, returncode), self._loop
        )

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
            if r.runner == self.launcher.name and r.runner_ref:
                try:
                    self.launcher.cancel(r.runner_ref)
                except Exception:
                    pass
            if await self._fail_if_active(
                r.id, "워커 응답이 없어 예약이 중단되었습니다."
            ):
                count += 1
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
