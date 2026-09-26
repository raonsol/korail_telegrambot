"""SSE: 예약 상태 실시간 스트림"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .deps import ServicesDep, SessionDep

router = APIRouter(prefix="/api", tags=["events"])

HEARTBEAT_SECONDS = 20


@router.get("/events")
async def events(request: Request, session: SessionDep, services: ServicesDep):
    broker = services.sse
    queue = broker.subscribe(session.user_id, session.is_admin)

    async def stream():
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                    yield f"event: reservation\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            broker.unsubscribe(session.user_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
