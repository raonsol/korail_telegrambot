"""열차 시간표 조회 (새 예약 화면에서 고른 시간대의 열차와 좌석 상황을 보여 줌)

코레일 열차 조회는 로그인이 필요 없으므로 비밀번호 없이, 그 계정의 예약과 같은
출구(IP)·기기 신원으로 요청한다. 출구별 요청 간격(KORAIL_EGRESS_RPM)과 차단 대기를
예약 워커와 함께 지키고, 같은 조건은 잠시 캐시해 코레일 요청을 줄인다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from pydantic import BaseModel

from .egress import EgressPool, NullGate
from .errors import KorailUnavailable, ServiceError
from .schemas import KST, now_kst

logger = logging.getLogger(__name__)

# 같은 조건 재조회는 이 시간 동안 캐시 (좌석 상황은 계속 바뀌므로 짧게)
CACHE_TTL_SECONDS = 30
CACHE_MAX_ENTRIES = 200
# 코레일은 한 번에 열차 10개 정도만 돌려주므로 시간대가 넓으면 이어서 조회 (출구 요청 수 제한)
MAX_PAGES = 3
# 출구 순서를 이만큼 넘게 기다려야 하면 조회하지 않음 (예약 워커가 출구를 많이 쓰는 중)
MAX_SLOT_WAIT_SECONDS = 8

BLOCKED_MESSAGE = (
    "코레일 서버가 요청을 일시적으로 차단해 열차를 조회할 수 없습니다. "
    "잠시 후 다시 시도해주세요."
)
BUSY_MESSAGE = "열차 조회 요청이 많습니다. 잠시 후 다시 시도해주세요."
FAILED_MESSAGE = "열차를 조회하지 못했습니다. 잠시 후 다시 시도해주세요."


class TrainOut(BaseModel):
    train_no: str  # "015"
    train_type: str  # "KTX", "KTX-산천", "ITX-새마을" ...
    dep_time: str  # HHMM
    arr_time: str  # HHMM
    duration_minutes: Optional[int] = None
    general_seat: bool
    special_seat: bool
    # 매진이지만 (일반실) 예약대기를 받는 열차
    waitlist: bool
    # 실제 출발·도착역 (인근역 포함 조회에서는 고른 역과 다를 수 있음)
    dep_station: Optional[str] = None
    arr_station: Optional[str] = None


class TrainSearchOut(BaseModel):
    trains: list[TrainOut]
    # MAX_PAGES 만큼 조회해도 시간대 끝까지 닿지 못함 (뒤쪽 열차가 더 있을 수 있음)
    truncated: bool = False


@dataclass(frozen=True)
class TrainQuery:
    dep_date: date
    src_station: str
    dst_station: str
    dep_time: str  # HHMM (이 시각 이후 출발)
    max_dep_time: str  # HHMM (이 시각 전 출발, 예약 조건과 같은 기준)
    train_type: str  # "KTX" | "ALL"
    # 인근역(코레일 앱의 "인접역") 출발·도착 열차도 포함 (예약 조건의 include_nearby)
    include_nearby: bool = False


class TrainSearchService:
    def __init__(
        self,
        egresses: EgressPool,
        device_for: Callable[[str], Optional[dict]] = lambda _: None,
        client_factory: Optional[Callable[..., object]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.egresses = egresses
        self.device_for = device_for
        self._client_factory = client_factory
        self._sleep = sleep
        self._clock = clock
        self._cache: dict[TrainQuery, tuple[float, TrainSearchOut]] = {}
        self._lock = threading.Lock()

    def search(self, korail_id: str, query: TrainQuery) -> TrainSearchOut:
        """블로킹 (코레일 HTTP 요청) - 웹에서는 스레드에서 호출"""
        depart_after = _depart_after(query)
        if depart_after is None:
            # 오늘 이미 지난 시간대
            return TrainSearchOut(trains=[])

        cached = self._cached(query)
        if cached is not None:
            return cached

        result = self._search_pages(korail_id, query, depart_after)
        with self._lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
            self._cache[query] = (self._clock(), result)
        return result

    def _cached(self, query: TrainQuery) -> Optional[TrainSearchOut]:
        with self._lock:
            hit = self._cache.get(query)
        if hit and self._clock() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
        return None

    def _search_pages(
        self, korail_id: str, query: TrainQuery, depart_after: datetime
    ) -> TrainSearchOut:
        from pykorail import AccessRestrictedError, NoResultsError, TrainType

        from telegramBot.korail_client import (
            APP_OUTDATED_MESSAGE,
            create_korail_client,
            is_app_outdated,
        )
        from .runner import TRAIN_TYPES

        egress = self.egresses.for_account(korail_id)
        gate = self.egresses.gate(egress.id) if egress else NullGate()
        factory = self._client_factory or create_korail_client
        client = factory(egress.proxy_url, device=self.device_for(korail_id))
        # 역 이름은 화면의 역 목록에서만 고르므로 역 마스터 확인 요청(조회마다 1회)을 생략
        client.trains.validate_stations = False

        trains: dict[tuple, TrainOut] = {}
        truncated = False
        try:
            for page in range(MAX_PAGES):
                self._wait_for_slot(gate)
                try:
                    found = client.trains.search(
                        query.src_station,
                        query.dst_station,
                        depart_after=depart_after,
                        train_type=TRAIN_TYPES.get(query.train_type, TrainType.KTX),
                        include_no_seats=True,
                        include_nearby_stations=query.include_nearby,
                    )
                except NoResultsError:
                    break
                in_range = [t for t in found if t.dep_time[:4] < query.max_dep_time]
                for train in in_range:
                    # 인근역 조회는 같은 열차가 역 조합마다 나오므로 역 코드까지 키에 포함
                    key = (
                        train.train_no,
                        train.dep_time,
                        getattr(train, "dep_code", None),
                        getattr(train, "arr_code", None),
                    )
                    trains.setdefault(key, _out(train))
                last = found[-1].dep_time
                if len(in_range) < len(found) or last[:4] >= query.max_dep_time:
                    break  # 시간대 끝을 지남
                next_after = datetime.strptime(
                    f"{query.dep_date:%Y%m%d}{last}", "%Y%m%d%H%M%S"
                ).replace(tzinfo=KST) + timedelta(minutes=1)
                if next_after.date() != query.dep_date or next_after <= depart_after:
                    break
                depart_after = next_after
                truncated = page == MAX_PAGES - 1
        except AccessRestrictedError as e:
            # 차단 중에 같은 출구로 계속 요청하면 차단이 길어지므로 예약 워커와 함께 쉼
            logger.error(
                "코레일 서버 차단 응답 (단계=열차 목록, 출구=%s): %s",
                egress.id or "direct",
                e,
            )
            gate.report_block()
            raise KorailUnavailable(BLOCKED_MESSAGE)
        except (KorailUnavailable, ServiceError):
            raise
        except Exception as e:
            if is_app_outdated(e):
                raise KorailUnavailable(
                    APP_OUTDATED_MESSAGE, code="KORAIL_APP_OUTDATED"
                )
            logger.warning(f"Train list search failed: {e}")
            raise ServiceError(FAILED_MESSAGE, code="TRAIN_SEARCH_FAILED")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        ordered = sorted(trains.values(), key=lambda t: t.dep_time)
        return TrainSearchOut(trains=ordered, truncated=truncated)

    def _wait_for_slot(self, gate) -> None:
        if gate.blocked_for() > 0:
            raise KorailUnavailable(BLOCKED_MESSAGE)
        delay = gate.reserve_slot()
        if delay > MAX_SLOT_WAIT_SECONDS:
            raise ServiceError(BUSY_MESSAGE, code="TRAIN_SEARCH_BUSY")
        if delay > 0:
            self._sleep(delay)


def _depart_after(query: TrainQuery) -> Optional[datetime]:
    """조회 시작 시각 (KST). 오늘의 지난 시각은 지금부터, 시간대가 모두 지났으면 None"""
    requested = datetime.strptime(
        f"{query.dep_date:%Y%m%d}{query.dep_time}", "%Y%m%d%H%M"
    ).replace(tzinfo=KST)
    now = now_kst()
    if requested >= now:
        return requested
    if query.dep_date != now.date() or now.strftime("%H%M") >= query.max_dep_time:
        return None
    return now


def _out(train) -> TrainOut:
    return TrainOut(
        train_no=train.train_no,
        train_type=train.train_type_name,
        dep_time=train.dep_time[:4],
        arr_time=train.arr_time[:4],
        duration_minutes=train.duration_minutes,
        general_seat=train.has_general_seat(),
        special_seat=train.has_special_seat(),
        waitlist=not train.has_seat() and train.has_waiting_list(),
        dep_station=_text(getattr(train, "dep_name", None)),
        arr_station=_text(getattr(train, "arr_name", None)),
    )


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None
