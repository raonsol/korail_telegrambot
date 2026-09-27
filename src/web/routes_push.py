"""Web Push 구독"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.db import utcnow
from core.errors import ServiceError
from core.models import PushSubscription

from .deps import ServicesDep, SessionDep

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=1024, pattern=r"^https://")
    keys: PushKeys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(max_length=1024)


@router.get("/config")
def push_config(_: SessionDep, services: ServicesDep):
    settings = services.settings
    return {
        "enabled": settings.push_enabled,
        "public_key": settings.vapid_public_key or None,
    }


@router.post("/subscriptions", status_code=204)
def subscribe(body: SubscriptionIn, session: SessionDep, services: ServicesDep):
    if not services.settings.push_enabled:
        raise ServiceError("푸시 알림이 설정되지 않았습니다.", code="PUSH_DISABLED")
    with services.db.session() as s:
        sub = s.scalar(
            select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
        )
        if sub is None:
            sub = PushSubscription(endpoint=body.endpoint, created_at=utcnow())
            s.add(sub)
        sub.user_id = session.user_id
        sub.p256dh = body.keys.p256dh
        sub.auth = body.keys.auth
    return Response(status_code=204)


@router.post("/unsubscribe", status_code=204)
def unsubscribe(body: UnsubscribeIn, session: SessionDep, services: ServicesDep):
    with services.db.session() as s:
        sub = s.scalar(
            select(PushSubscription).where(
                PushSubscription.endpoint == body.endpoint,
                PushSubscription.user_id == session.user_id,
            )
        )
        if sub:
            s.delete(sub)
    return Response(status_code=204)
