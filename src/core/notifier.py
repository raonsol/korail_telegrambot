"""예약 상태 알림 fan-out

예약 상태가 바뀌면 등록된 모든 채널(텔레그램, SSE, Web Push)로 전달합니다.
한 채널이 실패해도 다른 채널 전송은 계속됩니다.
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Protocol

from sqlalchemy import delete, select

from .db import Database
from .models import PushSubscription
from .schemas import ReservationOut, ReservationStatus

logger = logging.getLogger(__name__)


@dataclass
class ReservationEvent:
    reservation: ReservationOut
    previous_status: str
    # 상태 변경 주체: worker | web | telegram | admin | system
    source: str
    chat_id: Optional[int] = None

    @property
    def status(self) -> str:
        return self.reservation.status.value

    @property
    def is_terminal_change(self) -> bool:
        return (not self.reservation.is_active) and self.previous_status != self.status


class NotificationChannel(Protocol):
    async def deliver(self, event: ReservationEvent) -> None: ...


class Notifier:
    def __init__(self):
        self.channels: list[NotificationChannel] = []

    def add(self, channel: NotificationChannel) -> None:
        self.channels.append(channel)

    async def notify(self, event: ReservationEvent) -> None:
        for channel in self.channels:
            try:
                await channel.deliver(event)
            except Exception as e:
                logger.error(f"{type(channel).__name__} failed to deliver: {e}")


class SSEBroker:
    """앱이 열려 있는 동안 실시간 갱신 (프로세스 내 브로드캐스트)"""

    QUEUE_SIZE = 100

    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._admin_subscribers: set[asyncio.Queue] = set()

    def subscribe(self, owner_id: str, is_admin: bool = False) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_SIZE)
        if is_admin:
            self._admin_subscribers.add(queue)
        else:
            self._subscribers[owner_id].add(queue)
        return queue

    def unsubscribe(self, owner_id: str, queue: asyncio.Queue) -> None:
        self._admin_subscribers.discard(queue)
        subscribers = self._subscribers.get(owner_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(owner_id, None)

    def subscriber_count(self) -> int:
        return len(self._admin_subscribers) + sum(
            len(s) for s in self._subscribers.values()
        )

    async def deliver(self, event: ReservationEvent) -> None:
        data = event.reservation.model_dump_json()
        targets = set(self._subscribers.get(event.reservation.owner_id, ()))
        targets |= self._admin_subscribers
        for queue in targets:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("SSE queue full, dropping event")


def push_message(event: ReservationEvent) -> Optional[dict]:
    """Web Push 알림 내용 (종료 상태만 알림)"""
    r = event.reservation
    route = f"{r.src_station} → {r.dst_station}"
    date = f"{r.dep_date[4:6]}/{r.dep_date[6:]}"
    status = r.status
    if status == ReservationStatus.SUCCESS and r.waitlisted:
        title = "🕒 예약대기 신청 완료"
        body = f"{date} {route}\n좌석이 배정되면 코레일이 알려드립니다.".strip()
    elif status == ReservationStatus.SUCCESS:
        title = "🚄 예약 성공! 20분 안에 결제하세요"
        body = f"{date} {route}\n{r.result_text or ''}".strip()
    elif status == ReservationStatus.FAILED:
        title = "예약 실패"
        body = f"{date} {route}\n{r.error or '빈 좌석을 찾지 못했습니다.'}"
    elif status == ReservationStatus.ERROR:
        title = "예약 오류"
        body = f"{date} {route}\n{r.error or '예약 중 오류가 발생했습니다.'}"
    elif status == ReservationStatus.CANCELLED:
        title = "예약이 취소되었습니다"
        body = f"{date} {route}"
    else:
        return None
    return {"title": title, "body": body, "url": f"/app/r/{r.id}", "tag": r.id}


class WebPushChannel:
    """앱이 닫혀 있어도 결과를 알림 (VAPID)"""

    def __init__(self, db: Database, public_key: str, private_key: str, subject: str):
        self.db = db
        self.public_key = public_key
        self.private_key = private_key
        self.subject = subject

    def subscriptions(self, user_id: str) -> list[PushSubscription]:
        with self.db.session() as s:
            return list(
                s.scalars(
                    select(PushSubscription).where(PushSubscription.user_id == user_id)
                )
            )

    def _send(self, sub: PushSubscription, payload: dict) -> bool:
        """전송 후 구독이 유효한지 반환"""
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                ttl=60 * 60,
            )
            return True
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                return False
            logger.error(f"Web push failed ({status}): {e}")
            return True

    async def deliver(self, event: ReservationEvent) -> None:
        if not event.is_terminal_change:
            return
        payload = push_message(event)
        if not payload:
            return
        for sub in self.subscriptions(event.reservation.owner_id):
            valid = await asyncio.to_thread(self._send, sub, payload)
            if not valid:
                with self.db.session() as s:
                    s.execute(
                        delete(PushSubscription).where(PushSubscription.id == sub.id)
                    )
