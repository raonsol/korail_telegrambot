"""내 코레일 예약·승차권 ("내 승차권" 화면)

로그인한 코레일 계정(세션의 비밀번호)으로 코레일에 지금 있는 결제 전 예약과 발권된
승차권을 조회하고, 결제 전 예약을 취소한다. 코레일봇이 잡지 않은 예약도 포함된다.
"""

import asyncio

from fastapi import APIRouter, Path, Response

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


@router.post("/reservations/{rsv_id}/cancel", status_code=204)
async def cancel_korail_reservation(
    session: SessionDep,
    services: ServicesDep,
    rsv_id: str = Path(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z]+$"),
):
    """결제 전 예약(예약대기 포함) 취소

    최근 조회 결과(캐시)에서는 그 예약만 빠지므로 목록은 GET /account 로 다시 받으면 됨
    (코레일에 다시 묻지 않음)
    """
    korail_id, korail_pw = await asyncio.to_thread(services.auth.credentials, session)
    await services.reservations.cancel_korail_rsv(
        korail_id,
        korail_pw,
        rsv_id,
        source="admin" if session.is_admin else "web",
    )
    return Response(status_code=204)
