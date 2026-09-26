"""역 검색 (공공데이터포털 API, 결과 캐시)"""

import time

from fastapi import APIRouter, Query

from core.errors import ServiceError
from telegramBot.station_keyboard import NUM_OF_ROWS, search_stations

from .deps import SessionDep

router = APIRouter(prefix="/api/stations", tags=["stations"])

CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_ENTRIES = 500
_cache: dict[tuple[str, int], tuple[float, dict]] = {}


async def cached_search(query: str, page: int) -> dict:
    key = (query, page)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    result = await search_stations(query, page)
    if len(_cache) >= CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (now, result)
    return result


@router.get("")
async def stations(
    _: SessionDep,
    q: str = Query(min_length=1, max_length=20),
    page: int = Query(default=1, ge=1, le=100),
):
    try:
        result = await cached_search(q.strip(), page)
    except Exception:
        raise ServiceError("역 검색에 실패했습니다. 잠시 후 다시 시도해주세요.")
    return {**result, "page_size": NUM_OF_ROWS}
