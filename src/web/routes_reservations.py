"""예약 API"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Query

from core.schemas import ReservationOut, ReservationRequest

from .deps import ServicesDep, SessionDep

router = APIRouter(prefix="/api/reservations", tags=["reservations"])

StatusFilter = Literal["active", "history", "all"]


@router.get("", response_model=list[ReservationOut])
def list_reservations(
    session: SessionDep,
    services: ServicesDep,
    status: StatusFilter = "all",
    scope: Literal["mine", "all"] = "mine",
    limit: int = Query(default=100, ge=1, le=500),
):
    active = {"active": True, "history": False, "all": None}[status]
    return services.reservations.list(
        session.owner, active=active, scope_all=scope == "all", limit=limit
    )


@router.post("", response_model=ReservationOut, status_code=201)
async def create_reservation(
    body: ReservationRequest, session: SessionDep, services: ServicesDep
):
    # DB 조회 + 복호화는 블로킹 작업이라 스레드에서 수행
    korail_id, korail_pw = await asyncio.to_thread(services.auth.credentials, session)
    return await services.reservations.start(
        session.owner, body, korail_id, korail_pw, origin="web"
    )


@router.get("/{reservation_id}", response_model=ReservationOut)
def get_reservation(reservation_id: str, session: SessionDep, services: ServicesDep):
    return services.reservations.get(reservation_id, owner=session.owner)


@router.delete("/{reservation_id}", response_model=ReservationOut)
async def cancel_reservation(
    reservation_id: str, session: SessionDep, services: ServicesDep
):
    return await services.reservations.cancel(
        reservation_id,
        owner=session.owner,
        source="admin" if session.is_admin else "web",
    )


@router.delete("", response_model=list[ReservationOut])
async def cancel_all_reservations(
    session: SessionDep,
    services: ServicesDep,
    scope: Literal["mine", "all"] = "mine",
):
    return await services.reservations.cancel_many(
        owner=session.owner,
        scope_all=scope == "all",
        source="admin" if session.is_admin else "web",
    )
