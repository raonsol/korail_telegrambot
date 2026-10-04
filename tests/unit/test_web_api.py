"""웹앱 REST API (세션 쿠키, CSRF, 권한)"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.schemas import now_kst
from web.factory import create_app


def _future(days=7):
    return (now_kst().date() + timedelta(days=days)).isoformat()


RESERVATION = {
    "src_station": "서울",
    "dst_station": "부산",
    "dep_time": "0900",
    "max_dep_time": "1200",
    "train_type": "KTX",
    "seat_type": "general",
}


@pytest.fixture
def app(services, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "sw.js").write_text("// sw")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)")
    return create_app(
        services.settings, services, run_housekeeping=False, dist_dir=str(dist)
    )


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c


async def _login(client, phone="010-1234-5678", password="correct"):
    response = await client.post(
        "/api/auth/login", json={"phone": phone, "password": password}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


async def _admin_login(client):
    response = await client.post(
        "/api/auth/admin-login", json={"password": "test_admin_password"}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


class TestAuthApi:
    @pytest.mark.asyncio
    async def test_login_sets_httponly_cookie(self, client):
        response = await client.post(
            "/api/auth/login", json={"phone": "01012345678", "password": "correct"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["phone"] == "010-1234-5678"
        assert body["user"]["is_admin"] is False
        assert body["csrf_token"]
        cookie = response.headers["set-cookie"]
        assert "korail_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "correct" not in response.text

    @pytest.mark.asyncio
    async def test_login_errors(self, client):
        unregistered = await client.post(
            "/api/auth/login", json={"phone": "01099999999", "password": "correct"}
        )
        assert unregistered.status_code == 403
        assert unregistered.json()["code"] == "NOT_ALLOWED"

        wrong = await client.post(
            "/api/auth/login", json={"phone": "01012345678", "password": "bad"}
        )
        assert wrong.status_code == 401
        assert "남은 시도 2회" in wrong.json()["message"]

    @pytest.mark.asyncio
    async def test_forwarded_header_cannot_bypass_admin_throttle(self, client):
        """X-Forwarded-For를 매번 바꿔도 같은 접속지로 세어 관리자 비밀번호 대입을 막음"""
        for i in range(3):
            wrong = await client.post(
                "/api/auth/admin-login",
                json={"password": "guess"},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            )
            assert wrong.status_code == 401

        blocked = await client.post(
            "/api/auth/admin-login",
            json={"password": "guess"},
            headers={"X-Forwarded-For": "203.0.113.99"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "RATE_LIMITED"

    @pytest.mark.asyncio
    async def test_me_requires_session(self, client):
        response = await client.get("/api/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"

        await _login(client)
        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["id"] == "01012345678"

    @pytest.mark.asyncio
    async def test_logout(self, client):
        await _login(client)
        response = await client.post("/api/auth/logout")
        assert response.status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401

    @pytest.mark.asyncio
    async def test_csrf_required_for_writes(self, client):
        await _login(client)
        token = client.headers.pop("X-CSRF-Token")

        response = await client.post(
            "/api/reservations", json={**RESERVATION, "dep_date": _future()}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF"

        client.headers["X-CSRF-Token"] = token
        response = await client.post(
            "/api/reservations", json={**RESERVATION, "dep_date": _future()}
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_settings_update(self, client, services):
        await _login(client)
        response = await client.patch(
            "/api/auth/me/settings", json={"telegram_notify": False}
        )
        assert response.status_code == 200
        assert response.json()["user"]["telegram_notify"] is False
        assert services.users.get("01012345678").telegram_notify is False


class TestReservationApi:
    @pytest.mark.asyncio
    async def test_create_list_get_cancel(self, client, fake_launcher):
        await _login(client)

        created = await client.post(
            "/api/reservations", json={**RESERVATION, "dep_date": _future()}
        )
        assert created.status_code == 201
        reservation = created.json()
        assert reservation["status"] == "queued"
        assert reservation["seat_type_label"] == "일반실 우선 예약"
        assert reservation["created_at"].endswith("+00:00")
        # 세션에 보관한 비밀번호로 워커 실행
        assert fake_launcher.launched[0]["korail_pw"] == "correct"
        assert fake_launcher.launched[0]["korail_id"] == "010-1234-5678"

        active = await client.get("/api/reservations", params={"status": "active"})
        assert [r["id"] for r in active.json()] == [reservation["id"]]

        detail = await client.get(f"/api/reservations/{reservation['id']}")
        assert detail.json()["id"] == reservation["id"]

        cancelled = await client.delete(f"/api/reservations/{reservation['id']}")
        assert cancelled.json()["status"] == "cancelled"

        history = await client.get("/api/reservations", params={"status": "history"})
        assert [r["id"] for r in history.json()] == [reservation["id"]]

    @pytest.mark.asyncio
    async def test_validation_error_message(self, client):
        await _login(client)
        response = await client.post(
            "/api/reservations",
            json={**RESERVATION, "dep_date": _future(), "dst_station": "서울"},
        )
        assert response.status_code == 422
        assert response.json() == {
            "code": "VALIDATION",
            "message": "출발역과 도착역이 같습니다.",
        }

    @pytest.mark.asyncio
    async def test_limit_error(self, client):
        await _login(client)
        for _ in range(3):
            await client.post(
                "/api/reservations", json={**RESERVATION, "dep_date": _future()}
            )
        response = await client.post(
            "/api/reservations", json={**RESERVATION, "dep_date": _future()}
        )
        assert response.status_code == 429
        assert response.json()["code"] == "LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_users_cannot_see_each_other(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as alice, httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as bob:
            await _login(alice, "01012345678")
            await _login(bob, "01087654321")
            created = await alice.post(
                "/api/reservations", json={**RESERVATION, "dep_date": _future()}
            )
            rid = created.json()["id"]

            assert (await bob.get(f"/api/reservations/{rid}")).status_code == 404
            assert (await bob.delete(f"/api/reservations/{rid}")).status_code == 404
            assert (await bob.get("/api/reservations")).json() == []
            forbidden = await bob.get("/api/reservations", params={"scope": "all"})
            assert forbidden.status_code == 403

    @pytest.mark.asyncio
    async def test_cancel_all_mine(self, client):
        await _login(client)
        for _ in range(2):
            await client.post(
                "/api/reservations", json={**RESERVATION, "dep_date": _future()}
            )
        response = await client.delete("/api/reservations")
        assert len(response.json()) == 2
        active = await client.get("/api/reservations", params={"status": "active"})
        assert active.json() == []


class TestAdminApi:
    @pytest.mark.asyncio
    async def test_admin_manages_users(self, client, services):
        await _admin_login(client)

        users = await client.get("/api/admin/users")
        assert [u["phone"] for u in users.json()] == [
            "010-1234-5678",
            "010-8765-4321",
            "admin_user",  # ADMIN_KORAIL_ID 행
        ]

        created = await client.post(
            "/api/admin/users", json={"phone": "010-5555-6666", "name": "김철수"}
        )
        assert created.status_code == 201
        assert created.json()["name"] == "김철수"

        duplicate = await client.post("/api/admin/users", json={"phone": "01055556666"})
        assert duplicate.status_code == 409

        updated = await client.patch(
            "/api/admin/users/01055556666", json={"is_active": False}
        )
        assert updated.json()["is_active"] is False

        deleted = await client.delete("/api/admin/users/01055556666")
        assert deleted.status_code == 204
        assert services.users.get("01055556666") is None

    @pytest.mark.asyncio
    async def test_deactivation_revokes_sessions(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as user, httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as admin:
            await _login(user)
            await _admin_login(admin)
            await admin.patch("/api/admin/users/01012345678", json={"is_active": False})
            assert (await user.get("/api/auth/me")).status_code == 401

    @pytest.mark.asyncio
    async def test_non_admin_forbidden(self, client):
        await _login(client)
        assert (await client.get("/api/admin/users")).status_code == 403

    @pytest.mark.asyncio
    async def test_admin_sees_all_reservations(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as user, httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as admin:
            await _login(user)
            await user.post(
                "/api/reservations", json={**RESERVATION, "dep_date": _future()}
            )
            await _admin_login(admin)
            everyone = await admin.get("/api/reservations", params={"scope": "all"})
            assert len(everyone.json()) == 1
            cancelled = await admin.delete("/api/reservations", params={"scope": "all"})
            assert cancelled.json()[0]["status"] == "cancelled"


class TestStationsApi:
    @pytest.mark.asyncio
    async def test_search_is_cached(self, client):
        from web import routes_stations

        routes_stations._cache.clear()
        await _login(client)
        result = {
            "stations": [{"code": "1", "name": "광주송정"}],
            "total": 1,
            "page": 1,
        }
        with patch(
            "web.routes_stations.search_stations", new=AsyncMock(return_value=result)
        ) as search:
            first = await client.get("/api/stations", params={"q": "광주"})
            second = await client.get("/api/stations", params={"q": "광주"})

        assert first.json()["stations"][0]["name"] == "광주송정"
        assert second.json() == first.json()
        search.assert_awaited_once_with("광주", 1)

    @pytest.mark.asyncio
    async def test_search_failure(self, client):
        from web import routes_stations

        routes_stations._cache.clear()
        await _login(client)
        with patch(
            "web.routes_stations.search_stations",
            new=AsyncMock(side_effect=RuntimeError("api down")),
        ):
            response = await client.get("/api/stations", params={"q": "서울"})
        assert response.status_code == 400
        assert "역 검색에 실패" in response.json()["message"]


class TestPushApi:
    @pytest.mark.asyncio
    async def test_push_disabled(self, client):
        await _login(client)
        config = await client.get("/api/push/config")
        assert config.json() == {"enabled": False, "public_key": None}
        response = await client.post(
            "/api/push/subscriptions",
            json={"endpoint": "https://push", "keys": {"p256dh": "a", "auth": "b"}},
        )
        assert response.json()["code"] == "PUSH_DISABLED"

    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self, client, services):
        from core.models import PushSubscription
        from sqlalchemy import select

        services.settings = services.settings.model_copy(
            update={"vapid_public_key": "pub", "vapid_private_key": "priv"}
        )
        await _login(client)
        body = {"endpoint": "https://push/1", "keys": {"p256dh": "a", "auth": "b"}}
        assert (
            await client.post("/api/push/subscriptions", json=body)
        ).status_code == 204
        # 같은 endpoint 재등록은 갱신
        assert (
            await client.post("/api/push/subscriptions", json=body)
        ).status_code == 204
        with services.db.session() as s:
            assert len(list(s.scalars(select(PushSubscription)))) == 1

        response = await client.post(
            "/api/push/unsubscribe", json={"endpoint": "https://push/1"}
        )
        assert response.status_code == 204
        with services.db.session() as s:
            assert list(s.scalars(select(PushSubscription))) == []

    @pytest.mark.asyncio
    async def test_rejects_non_https_endpoint(self, client, services):
        services.settings = services.settings.model_copy(
            update={"vapid_public_key": "pub", "vapid_private_key": "priv"}
        )
        await _login(client)
        response = await client.post(
            "/api/push/subscriptions",
            json={"endpoint": "http://evil", "keys": {"p256dh": "a", "auth": "b"}},
        )
        assert response.status_code == 422


class TestEventsApi:
    @pytest.mark.asyncio
    async def test_requires_session(self, client):
        assert (await client.get("/api/events")).status_code == 401

    @pytest.mark.asyncio
    async def test_stream_delivers_reservation_updates(self, services, valid_request):
        """SSE 핸들러가 브로커 이벤트를 text/event-stream으로 전달"""
        import asyncio

        from core.schemas import Owner
        from starlette.requests import Request
        from web.routes_events import events

        token, session = await services.auth.login("01012345678", "correct")
        disconnected = asyncio.Event()
        scope = {"type": "http", "method": "GET", "headers": [], "path": "/api/events"}

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        request = Request(scope, receive)
        response = await events(request, session, services)
        stream = response.body_iterator

        assert (await stream.__anext__()).startswith("retry:")
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)
        await services.reservations.start(
            Owner(user_id="01012345678"), valid_request, "010", "pw", origin="web"
        )
        chunk = await asyncio.wait_for(pending, 2)
        assert chunk.startswith("event: reservation\ndata: {")
        assert '"status":"queued"' in chunk

        disconnected.set()
        await stream.aclose()
        assert services.sse.subscriber_count() == 0


class TestStaticWebapp:
    @pytest.mark.asyncio
    async def test_spa_routes(self, client):
        assert (await client.get("/")).headers["location"] == "/app/"
        index = await client.get("/app/")
        assert index.text == "<html>app</html>"
        deep = await client.get("/app/r/abc123")
        assert deep.text == "<html>app</html>"
        assert deep.headers["cache-control"] == "no-cache"

    @pytest.mark.asyncio
    async def test_assets_and_sw_cache_headers(self, client):
        asset = await client.get("/app/assets/index-abc.js")
        assert "immutable" in asset.headers["cache-control"]
        sw = await client.get("/app/sw.js")
        assert sw.headers["cache-control"] == "no-cache"
        missing = await client.get("/app/assets/missing.js")
        assert missing.status_code == 404

    @pytest.mark.asyncio
    async def test_no_path_traversal(self, client):
        response = await client.get("/app/..%2F..%2Fetc%2Fpasswd")
        assert "root:" not in response.text


class TestLifespan:
    @pytest.mark.asyncio
    async def test_startup_aborts_reservations_interrupted_by_restart(
        self, app, services, valid_request
    ):
        from core.schemas import Owner

        owner = Owner(user_id="01012345678")
        reservation = await services.reservations.start(
            owner, valid_request, "010", "pw", origin="web"
        )

        async with app.router.lifespan_context(app):
            r = services.reservations.get(reservation.id, owner)
            assert r.status.value == "error"
            assert "서버가 다시 시작" in r.error
