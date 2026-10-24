"""열차 시간표 조회 (새 예약 화면의 시간대별 열차 목록)"""

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Query

from core.errors import ValidationFailed
from core.schemas import MAX_DAYS_AHEAD, TrainTypeKey, _validate_hhmm, now_kst
from core.trains import TrainQuery, TrainSearchOut

from .deps import ServicesDep, SessionDep

router = APIRouter(prefix="/api/trains", tags=["trains"])


@router.get("", response_model=TrainSearchOut)
async def search_trains(
    session: SessionDep,
    services: ServicesDep,
    dep_date: date = Query(alias="date"),
    src: str = Query(min_length=1, max_length=30),
    dst: str = Query(min_length=1, max_length=30),
    start: str = Query(description="HHMM, 이 시각 이후 출발"),
    end: str = Query(description="HHMM, 이 시각 전 출발 (예약 조건과 같은 기준)"),
    train_type: TrainTypeKey = "KTX",
    nearby: bool = Query(default=False, description="인근역 출발·도착 열차도 포함"),
):
    query = _query(dep_date, src, dst, start, end, train_type, nearby)
    # 코레일 요청(블로킹)과 출구 순서 대기는 스레드에서
    return await asyncio.to_thread(services.trains.search, session.korail_id, query)


def _query(dep_date, src, dst, start, end, train_type, nearby=False) -> TrainQuery:
    try:
        start, end = _validate_hhmm(start), _validate_hhmm(end)
    except ValueError as e:
        raise ValidationFailed(str(e))
    src, dst = src.strip(), dst.strip()
    today = now_kst().date()
    if not src or not dst or src == dst:
        raise ValidationFailed("출발역과 도착역을 확인해주세요.")
    if not today <= dep_date <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise ValidationFailed("조회할 수 없는 출발일입니다.")
    if end <= start:
        raise ValidationFailed("끝 시각이 시작 시각보다 늦어야 합니다.")
    return TrainQuery(dep_date, src, dst, start, end, train_type, nearby)
