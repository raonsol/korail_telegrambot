"""워커 → 웹 서버 상태 보고 (예약별 콜백 토큰으로 인증)

기존 ``/completion/{chat_id}``, ``/reservation_callback`` 을 대체합니다.
리버스 프록시에서 ``/internal`` 경로는 외부에 노출하지 않는 것을 권장합니다.
"""

from fastapi import APIRouter

from core.schemas import WorkerEvent

from .deps import ServicesDep

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.post("/events")
async def worker_event(event: WorkerEvent, services: ServicesDep):
    applied = await services.reservations.handle_worker_event(event)
    return {"applied": applied}
