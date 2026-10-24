"""내 코레일 예약·승차권 ("내 승차권" 화면)

로그인한 코레일 계정(세션의 비밀번호)으로 코레일에 지금 있는 결제 전 예약과 발권된
승차권을 조회하고, 결제 전 예약을 취소한다. 코레일봇이 잡지 않은 예약도 포함된다.
"""

import asyncio

from fastapi import APIRouter, Path

from core.korail_account import KorailAccountOut

from .deps import ServicesDep, SessionDep

router = APIRouter(prefix="/api/korail", tags=["korail"])


@router.get("/account", response_model=KorailAccountOut)
async def korail_account(
    session: SessionDep, services: ServicesDep, refresh: bool = False
):
    """refresh=false 이면 같은 계정의 최근 조회 결과(60초)를 재사용"""
    korail_id, korail_pw = await asyncio.to_thread(services.auth.credentials, session)
    # 코레일 요청(블로킹)과 출구 순서 대기는 스레드에서
    return await asyncio.to_thread(
        services.korail_account.overview, korail_id, korail_pw, refresh
    )


@router.post("/reservations/{rsv_id}/cancel", response_model=KorailAccountOut)
async def cancel_korail_reservation(
    session: SessionDep,
    services: ServicesDep,
    rsv_id: str = Path(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z]+$"),
):
    """결제 전 예약(예약대기 포함)을 취소하고 남은 목록을 돌려줌

    방금 본 목록이 있으면 거기서 취소한 예약만 빼서 돌려줌 (다시 조회하면 로그인과
    예약마다 상세 조회가 또 나가므로). 없으면 새로 조회.
    """
    korail_id, korail_pw = await asyncio.to_thread(services.auth.credentials, session)
    before = services.korail_account.cached(korail_id)
    await services.reservations.cancel_korail_rsv(
        korail_id,
        korail_pw,
        rsv_id,
        source="admin" if session.is_admin else "web",
    )
    if before is not None:
        return services.korail_account.without(before, rsv_id)
    return await asyncio.to_thread(
        services.korail_account.overview, korail_id, korail_pw, True
    )
