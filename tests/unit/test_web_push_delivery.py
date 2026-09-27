"""Web Push 실제 전송 검증 (로컬 HTTP 서버를 푸시 서비스로 사용)"""

import base64
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from core.models import PushSubscription
from core.notifier import ReservationEvent, WebPushChannel
from core.schemas import Owner, ReservationStatus
from core.vapid import generate_vapid_keys


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class _PushService(BaseHTTPRequestHandler):
    received = []
    status = 201

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        headers = {k.lower(): v for k, v in self.headers.items()}
        type(self).received.append((headers, self.rfile.read(length)))
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def push_service():
    _PushService.received = []
    _PushService.status = 201
    server = HTTPServer(("127.0.0.1", 0), _PushService)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _PushService, f"http://127.0.0.1:{server.server_port}/push/abc"
    server.shutdown()
    server.server_close()


def _browser_keys():
    """브라우저 PushSubscription의 p256dh/auth 흉내"""
    key = ec.generate_private_key(ec.SECP256R1())
    p256dh = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(p256dh), _b64url(os.urandom(16))


class TestWebPushDelivery:
    def test_generated_keys_format(self):
        public_key, private_key = generate_vapid_keys()
        assert len(base64.urlsafe_b64decode(public_key + "==")) == 65
        assert len(base64.urlsafe_b64decode(private_key + "=")) == 32

    @pytest.mark.asyncio
    async def test_success_push_is_signed_and_encrypted(
        self, services, fake_launcher, valid_request, push_service
    ):
        handler, endpoint = push_service
        public_key, private_key = generate_vapid_keys()
        p256dh, auth = _browser_keys()
        with services.db.session() as s:
            s.add(
                PushSubscription(
                    user_id="01012345678", endpoint=endpoint, p256dh=p256dh, auth=auth
                )
            )
        channel = WebPushChannel(services.db, public_key, private_key, "mailto:a@b.c")

        owner = Owner(user_id="01012345678")
        reservation = await services.reservations.start(
            owner, valid_request, "010", "pw", origin="web"
        )
        done = reservation.model_copy(
            update={
                "status": ReservationStatus.SUCCESS,
                "is_active": False,
                "result_text": "KTX 101",
            }
        )
        await channel.deliver(ReservationEvent(done, "running", source="worker"))

        [(headers, body)] = handler.received
        assert headers["content-encoding"] == "aes128gcm"
        assert headers["authorization"].startswith("vapid t=")
        assert f"k={public_key}" in headers["authorization"]
        assert b"KTX 101" not in body  # 페이로드는 암호화됨
        assert len(channel.subscriptions("01012345678")) == 1

    @pytest.mark.asyncio
    async def test_expired_subscription_is_removed(
        self, services, valid_request, push_service
    ):
        handler, endpoint = push_service
        handler.status = 410
        public_key, private_key = generate_vapid_keys()
        p256dh, auth = _browser_keys()
        with services.db.session() as s:
            s.add(
                PushSubscription(
                    user_id="01012345678", endpoint=endpoint, p256dh=p256dh, auth=auth
                )
            )
        channel = WebPushChannel(services.db, public_key, private_key, "mailto:a@b.c")
        reservation = await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )
        done = reservation.model_copy(
            update={"status": ReservationStatus.FAILED, "is_active": False}
        )

        await channel.deliver(ReservationEvent(done, "running", source="worker"))

        assert channel.subscriptions("01012345678") == []
