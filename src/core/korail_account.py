"""내 코레일 예약·승차권 조회 (웹 "내 승차권" 화면, 텔레그램 /mytickets)

코레일 계정에 지금 있는 결제 대기 예약(예약대기 포함)과 발권된 승차권을 보여 준다.
코레일봇이 잡지 않은 예약(코레일톡 등에서 직접 잡은 것)도 함께 나온다.

- 비밀번호는 호출 측이 넘긴 것만 쓴다 (웹: 로그인 세션, 텔레그램: 대화의 로그인 정보)
- 그 계정의 예약과 같은 출구(IP)·기기 신원으로 요청하고, 출구별 요청 간격과 차단 대기를
  예약 워커와 함께 지킨다 (로그인 앞, 그리고 pykorail 이 보내는 목록·상세 요청마다 출구 순서를 받음)
- 예약·승차권마다 상세를 한 번씩 더 조회하므로 (pykorail) 같은 계정은 잠시 캐시한다
- 코레일이 비밀번호를 거절하면 같은 비밀번호로는 잠시 다시 로그인하지 않는다
  (웹 세션의 비밀번호가 바뀐 뒤 새로고침을 거듭해 코레일 계정이 잠기지 않도록)
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, field_serializer

from .db import utcnow
from .egress import EgressPool
from .auth import raise_login_failure
from .errors import KorailUnavailable, ServiceError
from .schemas import account_key, pay_deadline_utc

logger = logging.getLogger(__name__)

# 같은 계정을 다시 열면 이 시간 동안은 코레일에 묻지 않음 (새로고침은 예외)
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 200
# 출구 순서를 이만큼 넘게 기다려야 하면 조회하지 않음 (예약 워커가 출구를 많이 쓰는 중)
MAX_SLOT_WAIT_SECONDS = 8
# 코레일이 비밀번호를 거절한 뒤 같은 비밀번호로 다시 로그인하지 않는 시간 (코레일은 5회 실패 시 잠금)
LOGIN_REJECTED_HOLD_SECONDS = 600

BLOCKED_MESSAGE = (
    "코레일 서버가 요청을 일시적으로 차단해 예약·승차권을 불러올 수 없습니다. "
    "잠시 후 다시 시도해주세요."
)
BUSY_MESSAGE = "코레일 조회 요청이 많습니다. 잠시 후 다시 시도해주세요."
FAILED_MESSAGE = "코레일 예약·승차권을 불러오지 못했습니다. 잠시 후 다시 시도해주세요."
LOGIN_HELD_MESSAGE = (
    "코레일이 이 비밀번호로 로그인을 거절했습니다. 코레일 비밀번호를 바꿨다면 로그아웃 후 "
    "다시 로그인해주세요. (계정 잠김을 막기 위해 10분 동안 같은 비밀번호로 다시 시도하지 않습니다)"
)


def _password_digest(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _pace_korail_requests(client, before: Callable[[], None]) -> None:
    """이 클라이언트가 코레일에 보내는 요청마다 먼저 before() (출구 순서) 를 거치게 함

    pykorail 은 목록 한 번에 항목마다 상세 요청을 더 보내므로 (N+1) 목록 호출 앞에서만
    순서를 받으면 출구 요청 간격(KORAIL_EGRESS_RPM)을 넘는다. 프록시를 넣는 곳과 같은
    curl_cffi 세션(_http_session)의 get/post 를 이 인스턴스에서만 감싼다.
    """
    from telegramBot.korail_client import _http_session

    session = _http_session(client)
    for name in ("get", "post"):
        send = getattr(session, name)

        def paced(url, _send=send, **kwargs):
            before()
            return _send(url, **kwargs)

        setattr(session, name, paced)


def _as_utc(value: Optional[datetime]):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class KorailReservationOut(BaseModel):
    """결제 전 예약 (예약대기 포함)"""

    rsv_id: str
    train_no: str
    train_type: str  # "KTX", "KTX-산천" ...
    dep_date: str  # YYYYMMDD
    dep_time: str  # HHMM
    arr_time: str  # HHMM
    dep_station: str
    arr_station: str
    price: int
    seat_count: int
    # 좌석이 아니라 예약대기 (결제 기한 없음)
    waiting: bool
    pay_deadline: Optional[datetime] = None
    # 코레일봇이 잡은 예약이면 그 예약 기록 id
    reservation_id: Optional[str] = None

    @field_serializer("pay_deadline")
    def _deadline(self, value):
        return _as_utc(value)


class KorailTicketOut(BaseModel):
    """발권된 승차권 (원권 번호·반환 비밀번호는 싣지 않음)"""

    rsv_id: str
    train_no: str
    train_type: str
    dep_date: str
    dep_time: str
    arr_time: str
    dep_station: str
    arr_station: str
    car_no: str
    seats: str  # "12A" 또는 "12A~12B"
    seat_count: int
    price: int
    reservation_id: Optional[str] = None


class KorailAccountOut(BaseModel):
    korail_id: str
    reservations: list[KorailReservationOut]
    tickets: list[KorailTicketOut]
    fetched_at: datetime

    @field_serializer("fetched_at")
    def _fetched(self, value):
        return _as_utc(value)


class KorailAccountService:
    def __init__(
        self,
        egresses: EgressPool,
        device_for: Callable[[str], Optional[dict]] = lambda _: None,
        links_for: Callable[[str, set[str]], dict[str, str]] = lambda *_: {},
        handler_factory: Optional[Callable[..., object]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        pace: Callable[[object, Callable[[], None]], None] = _pace_korail_requests,
    ):
        self.egresses = egresses
        self.device_for = device_for
        # (계정, 코레일 예약번호들) -> {코레일 예약번호: 코레일봇 예약 기록 id}
        # (ReservationService.korail_links)
        self.links_for = links_for
        self._handler_factory = handler_factory
        self._sleep = sleep
        self._clock = clock
        self._pace = pace
        self._cache: dict[str, tuple[float, KorailAccountOut]] = {}
        # forget() 마다 올림: 조회 중에 취소가 있었으면 그 조회 결과는 캐시하지 않음
        self._generation: dict[str, int] = {}
        # 계정 -> (거절된 시각, 비밀번호 해시)
        self._rejected: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 캐시

    def cached(self, korail_id: str) -> Optional[KorailAccountOut]:
        key = account_key(korail_id)
        with self._lock:
            hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
        return None

    def forget(self, korail_id: str) -> None:
        """예약을 취소하는 등 코레일 쪽이 바뀌었으면 다음 조회는 새로 받음"""
        key = account_key(korail_id)
        with self._lock:
            self._cache.pop(key, None)
            self._generation[key] = self._generation.get(key, 0) + 1

    @staticmethod
    def without(account: KorailAccountOut, rsv_id: str) -> KorailAccountOut:
        """취소한 예약을 뺀 목록 (화면 갱신용, 캐시에는 넣지 않음)"""
        return account.model_copy(
            update={
                "reservations": [r for r in account.reservations if r.rsv_id != rsv_id]
            }
        )

    # ------------------------------------------------------------------ 조회

    def overview(
        self, korail_id: str, password: str, refresh: bool = False
    ) -> KorailAccountOut:
        """블로킹 (코레일 HTTP 요청) - 웹에서는 스레드에서 호출"""
        if not refresh:
            cached = self.cached(korail_id)
            if cached is not None:
                return cached
        key = account_key(korail_id)
        digest = _password_digest(password)
        with self._lock:
            rejected = self._rejected.get(key)
            generation = self._generation.get(key, 0)
        if (
            rejected
            and rejected[1] == digest
            and self._clock() - rejected[0] < LOGIN_REJECTED_HOLD_SECONDS
        ):
            raise ServiceError(LOGIN_HELD_MESSAGE, code="KORAIL_LOGIN_FAILED")

        reservations, tickets = self._fetch(korail_id, password, key, digest)
        rsv_ids = {r.rsv_id for r in reservations} | {t.pnr_no for t in tickets}
        links = self.links_for(korail_id, rsv_ids) if rsv_ids else {}
        out = KorailAccountOut(
            korail_id=korail_id,
            reservations=[_reservation_out(r, links) for r in reservations],
            tickets=[_ticket_out(t, links) for t in tickets],
            fetched_at=utcnow(),
        )
        with self._lock:
            if self._generation.get(key, 0) == generation:
                self._store(key, out)
        return out

    def _store(self, key: str, out: KorailAccountOut) -> None:
        """self._lock 안에서 호출"""
        now = self._clock()
        for k in [
            k for k, (t, _) in self._cache.items() if now - t >= CACHE_TTL_SECONDS
        ]:
            del self._cache[k]
        # 같은 계정은 지우고 다시 넣어 가장 최근 항목으로 (넘치면 가장 오래된 것부터 버림)
        self._cache.pop(key, None)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (now, out)

    def _fetch(self, korail_id: str, password: str, key: str, digest: str):
        from pykorail import AccessRestrictedError, KorailError

        from telegramBot.korail_client import (
            APP_OUTDATED_MESSAGE,
            ReserveHandler,
            is_app_outdated,
        )

        egress = self.egresses.for_account(korail_id)
        gate = self.egresses.gate(egress.id)
        factory = self._handler_factory or ReserveHandler
        handler = factory(
            egress.proxy_url, egress.id, device=self.device_for(korail_id)
        )
        try:
            self._wait_for_slot(gate)
            if not handler.login(korail_id, password):
                if getattr(handler, "loginRejected", False) is True:
                    with self._lock:
                        self._rejected[key] = (self._clock(), digest)
                raise_login_failure(handler, gate)
            with self._lock:
                self._rejected.pop(key, None)
            client = handler.korail_client
            try:
                self._pace(client, lambda: self._wait_for_slot(gate))
                reservations = client.reservations.all()
                tickets = client.tickets.all()
            except AccessRestrictedError as e:
                # 차단 중에 같은 출구로 계속 요청하면 차단이 길어지므로 예약 워커와 함께 쉼
                logger.error(
                    "코레일 서버 차단 응답 (단계=내 승차권, 출구=%s): %s",
                    egress.id or "direct",
                    e,
                )
                gate.report_block()
                raise KorailUnavailable(BLOCKED_MESSAGE)
            except KorailError as e:
                if is_app_outdated(e):
                    raise KorailUnavailable(
                        APP_OUTDATED_MESSAGE, code="KORAIL_APP_OUTDATED"
                    )
                logger.warning(f"Korail account lookup refused: {e}")
                raise ServiceError(
                    f"코레일이 조회를 거절했습니다: {e.msg or e}",
                    code="KORAIL_ACCOUNT_FAILED",
                )
            except (KorailUnavailable, ServiceError):
                raise
            except Exception as e:  # 연결 실패, 코레일 외 HTTP 오류 등
                logger.warning(f"Korail account lookup failed: {e}")
                raise ServiceError(FAILED_MESSAGE, code="KORAIL_ACCOUNT_FAILED")
            return reservations, tickets
        finally:
            handler.close()

    def _wait_for_slot(self, gate) -> None:
        if gate.blocked_for() > 0:
            raise KorailUnavailable(BLOCKED_MESSAGE)
        delay = gate.reserve_slot()
        if delay > MAX_SLOT_WAIT_SECONDS:
            raise ServiceError(BUSY_MESSAGE, code="KORAIL_ACCOUNT_BUSY")
        if delay > 0:
            self._sleep(delay)


def _train_fields(train) -> dict:
    return {
        "train_no": train.train_no,
        "train_type": train.train_type_name,
        "dep_date": train.dep_date,
        "dep_time": train.dep_time[:4],
        "arr_time": train.arr_time[:4],
        "dep_station": train.dep_name,
        "arr_station": train.arr_name,
    }


def _reservation_out(r, links: dict[str, str]) -> KorailReservationOut:
    from telegramBot.korail_client import reservation_details

    _, deadline = reservation_details(r)
    return KorailReservationOut(
        rsv_id=r.rsv_id,
        **_train_fields(r.train),
        price=r.price,
        seat_count=r.seat_no_count,
        waiting=r.is_waiting,
        pay_deadline=pay_deadline_utc(deadline),
        reservation_id=links.get(r.rsv_id),
    )


def _ticket_out(t, links: dict[str, str]) -> KorailTicketOut:
    seats = (
        t.seat_no
        if t.seat_no_count == 1 or not t.seat_no_end
        else f"{t.seat_no}~{t.seat_no_end}"
    )
    return KorailTicketOut(
        rsv_id=t.pnr_no,
        **_train_fields(t.train),
        car_no=t.car_no,
        seats=seats or "",
        seat_count=t.seat_no_count,
        price=t.price,
        reservation_id=links.get(t.pnr_no),
    )
