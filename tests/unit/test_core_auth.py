"""웹 로그인/세션"""

from datetime import timedelta

import pytest

from core.crypto import CredentialVault, sha256_hex
from core.db import utcnow
from core.errors import AuthFailed, NotAllowed, RateLimited
from core.models import WebSession


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, services, korail_login):
        token, session = await services.auth.login(
            "010-1234-5678", "correct", remember=True, ip="1.1.1.1"
        )

        assert korail_login.calls == [("010-1234-5678", "correct")]
        assert session.user_id == "01012345678"
        assert not session.is_admin
        # DB에는 토큰 해시와 암호화된 비밀번호만 저장
        with services.db.session() as s:
            stored = s.get(WebSession, sha256_hex(token))
            assert stored is not None
            assert "correct" not in stored.korail_pw_enc
        assert services.auth.credentials(session) == ("010-1234-5678", "correct")
        assert services.users.get("01012345678").last_login_at is not None

    @pytest.mark.asyncio
    async def test_unregistered_user(self, services, korail_login):
        alerts = []

        async def alert(msg):
            alerts.append(msg)

        services.auth.alert = alert
        with pytest.raises(NotAllowed):
            await services.auth.login("01099999999", "correct")
        assert korail_login.calls == []  # 코레일에 로그인 시도하지 않음
        assert "010-9999-9999" in alerts[0]

    @pytest.mark.asyncio
    async def test_invalid_phone(self, services):
        with pytest.raises(AuthFailed) as exc:
            await services.auth.login("hello", "correct")
        assert exc.value.code == "INVALID_PHONE"

    @pytest.mark.asyncio
    async def test_failures_are_throttled_before_korail_lockout(
        self, services, korail_login
    ):
        for remaining in (2, 1, 0):
            with pytest.raises(AuthFailed, match=f"남은 시도 {remaining}회"):
                await services.auth.login("01012345678", "wrong")

        with pytest.raises(RateLimited):
            await services.auth.login("01012345678", "correct")
        # 코레일 5회 잠금 전에 차단됨
        assert len(korail_login.calls) == 3

        # 다른 사용자는 영향 없음
        await services.auth.login("01087654321", "correct")

    @pytest.mark.asyncio
    async def test_success_resets_failures(self, services, korail_login):
        with pytest.raises(AuthFailed):
            await services.auth.login("01012345678", "wrong")
        await services.auth.login("01012345678", "correct")
        assert services.auth.throttle.count("phone:01012345678") == 0

    @pytest.mark.asyncio
    async def test_admin_login(self, services, korail_login):
        _, session = await services.auth.admin_login("test_admin_password")
        assert session.is_admin
        assert session.user_id == "admin"
        assert korail_login.calls == [("admin_user", "admin_pass")]

    @pytest.mark.asyncio
    async def test_admin_login_wrong_password(self, services):
        for _ in range(3):
            with pytest.raises(AuthFailed):
                await services.auth.admin_login("nope", ip="9.9.9.9")
        with pytest.raises(RateLimited):
            await services.auth.admin_login("test_admin_password", ip="9.9.9.9")


class TestSessions:
    @pytest.mark.asyncio
    async def test_resolve_and_logout(self, services):
        token, session = await services.auth.login("01012345678", "correct")

        resolved = services.auth.resolve(token)
        assert resolved.user_id == session.user_id
        assert resolved.csrf_token == session.csrf_token
        assert services.auth.resolve("unknown") is None
        assert services.auth.resolve(None) is None

        services.auth.logout(token)
        assert services.auth.resolve(token) is None

    @pytest.mark.asyncio
    async def test_expired_session(self, services):
        token, _ = await services.auth.login("01012345678", "correct")
        with services.db.session() as s:
            s.get(WebSession, sha256_hex(token)).expires_at = utcnow() - timedelta(
                seconds=1
            )
        assert services.auth.resolve(token) is None
        assert services.auth.purge_expired() == 0  # resolve가 이미 삭제

    @pytest.mark.asyncio
    async def test_sliding_expiry_for_remembered_sessions(self, services):
        token, _ = await services.auth.login("01012345678", "correct", remember=True)
        with services.db.session() as s:
            s.get(WebSession, sha256_hex(token)).expires_at = utcnow() + timedelta(
                hours=1
            )
        resolved = services.auth.resolve(token)
        assert resolved.expires_at > utcnow() + timedelta(days=6)

    @pytest.mark.asyncio
    async def test_short_session_without_remember(self, services):
        _, session = await services.auth.login("01012345678", "correct", remember=False)
        assert session.expires_at < utcnow() + timedelta(hours=13)

    @pytest.mark.asyncio
    async def test_deactivated_user_loses_session(self, services):
        token, _ = await services.auth.login("01012345678", "correct")
        services.users.update("01012345678", is_active=False)
        assert services.auth.resolve(token) is None

    @pytest.mark.asyncio
    async def test_credentials_require_same_key(self, services):
        _, session = await services.auth.login("01012345678", "correct")
        services.auth.vault = CredentialVault("another-key")
        with pytest.raises(AuthFailed) as exc:
            services.auth.credentials(session)
        assert exc.value.code == "SESSION_EXPIRED"

    @pytest.mark.asyncio
    async def test_revoke_user_sessions(self, services):
        token, _ = await services.auth.login("01012345678", "correct")
        services.auth.revoke_user_sessions("01012345678")
        assert services.auth.resolve(token) is None


class TestConnectionUsage:
    @pytest.mark.asyncio
    async def test_resolve_uses_single_connection(self, test_settings, tmp_path):
        """요청당 DB 연결을 하나만 사용 (중첩 세션은 동시 요청 시 풀 고갈 → 교착)"""
        from sqlalchemy import event

        from core.db import Database
        from core.services import build_services

        db = Database(f"sqlite:///{tmp_path}/pool.db")
        svc = build_services(test_settings, db=db, korail_login=lambda i, p: True)
        svc.init_storage()
        token, _ = await svc.auth.login("01012345678", "x")

        in_use = {"now": 0, "max": 0}

        def checkout(*_):
            in_use["now"] += 1
            in_use["max"] = max(in_use["max"], in_use["now"])

        def checkin(*_):
            in_use["now"] -= 1

        event.listen(db.engine, "checkout", checkout)
        event.listen(db.engine, "checkin", checkin)
        try:
            assert svc.auth.resolve(token) is not None
        finally:
            db.dispose()
        assert in_use["max"] == 1


class TestVault:
    def test_roundtrip_and_key_isolation(self):
        vault = CredentialVault("k1")
        encrypted = vault.encrypt("secret")
        assert encrypted != "secret"
        assert vault.decrypt(encrypted) == "secret"
        assert CredentialVault("k1").decrypt(encrypted) == "secret"
        assert CredentialVault("k2").decrypt(encrypted) is None
        assert vault.persistent is True
        assert CredentialVault("").persistent is False
