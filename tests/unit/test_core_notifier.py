"""알림 fan-out: SSE, Web Push"""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from core.models import PushSubscription
from core.notifier import (
    Notifier,
    ReservationEvent,
    SSEBroker,
    WebPushChannel,
    push_message,
)
from core.schemas import ReservationOut


def _reservation(status="success", owner="01012345678", **extra):
    now = datetime(2025, 1, 1, 0, 0, 0)
    data = dict(
        id="r1",
        owner_id=owner,
        origin="web",
        status=status,
        dep_date="20250115",
        src_station="서울",
        dst_station="부산",
        dep_time="0900",
        max_dep_time="1200",
        train_type="KTX",
        seat_type="general",
        train_type_label="KTX",
        seat_type_label="일반실 우선 예약",
        attempts=3,
        result_text="KTX 101",
        created_at=now,
        updated_at=now,
        is_active=status in ("queued", "running"),
    )
    data.update(extra)
    return ReservationOut(**data)


def _event(status="success", previous="running", **extra):
    return ReservationEvent(_reservation(status, **extra), previous, source="worker")


class TestNotifier:
    @pytest.mark.asyncio
    async def test_failing_channel_does_not_block_others(self):
        class Broken:
            async def deliver(self, event):
                raise RuntimeError("down")

        received = []

        class Ok:
            async def deliver(self, event):
                received.append(event)

        notifier = Notifier()
        notifier.add(Broken())
        notifier.add(Ok())
        await notifier.notify(_event())
        assert len(received) == 1

    def test_terminal_change(self):
        assert _event("success", "running").is_terminal_change
        assert not _event("running", "queued").is_terminal_change
        assert not _event("success", "success").is_terminal_change


class TestSSEBroker:
    @pytest.mark.asyncio
    async def test_owner_and_admin_subscribers(self):
        broker = SSEBroker()
        mine = broker.subscribe("01012345678")
        other = broker.subscribe("01087654321")
        admin = broker.subscribe("admin", is_admin=True)

        await broker.deliver(_event())

        assert json.loads(mine.get_nowait())["id"] == "r1"
        assert json.loads(admin.get_nowait())["status"] == "success"
        assert other.empty()

        broker.unsubscribe("01012345678", mine)
        broker.unsubscribe("admin", admin)
        broker.unsubscribe("01087654321", other)
        assert broker.subscriber_count() == 0

    @pytest.mark.asyncio
    async def test_full_queue_drops(self):
        broker = SSEBroker()
        broker.QUEUE_SIZE = 1
        queue = broker.subscribe("01012345678")
        await broker.deliver(_event())
        await broker.deliver(_event())
        assert queue.qsize() == 1


class TestWebPush:
    def test_push_message(self):
        msg = push_message(_event("success"))
        assert "예약 성공" in msg["title"]
        assert "서울 → 부산" in msg["body"]
        assert msg["url"] == "/app/r/r1"
        assert push_message(_event("running", "queued")) is None
        assert "취소" in push_message(_event("cancelled"))["title"]
        assert "실패" in push_message(_event("failed", error="x"))["title"]

    @pytest.mark.asyncio
    async def test_sends_to_owner_subscriptions_and_prunes_gone(self, services):
        with services.db.session() as s:
            s.add(
                PushSubscription(
                    user_id="01012345678", endpoint="https://a", p256dh="k", auth="a"
                )
            )
            s.add(
                PushSubscription(
                    user_id="01012345678", endpoint="https://gone", p256dh="k", auth="a"
                )
            )
            s.add(
                PushSubscription(
                    user_id="01087654321", endpoint="https://b", p256dh="k", auth="a"
                )
            )
        channel = WebPushChannel(services.db, "pub", "priv", "mailto:x@y.z")

        from pywebpush import WebPushException

        sent = []

        def fake_webpush(subscription_info, data, **kwargs):
            sent.append(subscription_info["endpoint"])
            if subscription_info["endpoint"] == "https://gone":
                raise WebPushException("gone", response=Mock(status_code=410))

        with patch("pywebpush.webpush", side_effect=fake_webpush):
            await channel.deliver(_event("success"))
            await channel.deliver(_event("running", "queued"))  # 무시

        assert sorted(sent) == ["https://a", "https://gone"]
        assert [s.endpoint for s in channel.subscriptions("01012345678")] == [
            "https://a"
        ]
