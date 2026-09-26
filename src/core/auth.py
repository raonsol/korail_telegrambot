"""웹 로그인/세션

- 로그인: 전화번호가 사용자 DB에 등록·활성 상태인지 확인 → 실패 횟수 제한 → 코레일 로그인
- 세션: 서버 저장 (쿠키에는 불투명 토큰, DB에는 SHA-256 해시)
- 코레일 비밀번호는 예약 실행을 위해 암호화하여 세션에 보관, 로그아웃/만료 시 삭제
"""

import asyncio
import hmac
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from sqlalchemy import delete

from .crypto import CredentialVault, new_token, sha256_hex
from .db import Database, utcnow
from .errors import AuthFailed, NotAllowed, RateLimited, ServiceError
from .models import User, WebSession
from .schemas import ADMIN_USER_ID, Owner, format_phone, is_valid_phone, normalize_phone
from .users import UserService

logger = logging.getLogger(__name__)

KorailLogin = Callable[[str, str], bool]
Alert = Callable[[str], Awaitable[None]]

# "로그인 유지"를 끈 경우 세션 수명
SHORT_SESSION_HOURS = 12
# IP 기준 실패 한도 (전화번호 기준 한도와 별도)
IP_MAX_FAILURES = 20


def default_korail_login(korail_id: str, password: str) -> bool:
    from telegramBot.korail_client import ReserveHandler

    return ReserveHandler().login(korail_id, password)


@dataclass
class SessionInfo:
    session_id: str
    user_id: str
    is_admin: bool
    korail_id: str
    csrf_token: str
    remember: bool
    expires_at: datetime

    @property
    def owner(self) -> Owner:
        return Owner(user_id=self.user_id, is_admin=self.is_admin)

    @classmethod
    def from_model(cls, m: WebSession) -> "SessionInfo":
        return cls(
            session_id=m.id,
            user_id=m.user_id,
            is_admin=m.is_admin,
            korail_id=m.korail_id,
            csrf_token=m.csrf_token,
            remember=m.remember,
            expires_at=m.expires_at,
        )


class LoginThrottle:
    """메모리 기반 실패 횟수 제한 (코레일은 5회 실패 시 계정 잠금)"""

    def __init__(self, window_seconds: int):
        self.window = window_seconds
        self._failures: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque:
        q = self._failures[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def count(self, key: str) -> int:
        return len(self._prune(key, time.monotonic()))

    def retry_after(self, key: str) -> int:
        q = self._prune(key, time.monotonic())
        if not q:
            return 0
        return max(1, int(q[0] + self.window - time.monotonic()))

    def fail(self, key: str) -> int:
        now = time.monotonic()
        q = self._prune(key, now)
        q.append(now)
        return len(q)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


class AuthService:
    def __init__(
        self,
        db: Database,
        users: UserService,
        vault: CredentialVault,
        *,
        admin_password: str,
        admin_korail_id: Optional[str],
        admin_korail_pw: Optional[str],
        session_ttl_hours: int = 168,
        max_failures: int = 3,
        lock_minutes: int = 10,
        korail_login: KorailLogin = default_korail_login,
        alert: Optional[Alert] = None,
    ):
        self.db = db
        self.users = users
        self.vault = vault
        self.admin_password = admin_password
        self.admin_korail_id = admin_korail_id
        self.admin_korail_pw = admin_korail_pw
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self.max_failures = max_failures
        self.korail_login = korail_login
        self.alert = alert
        self.throttle = LoginThrottle(lock_minutes * 60)

    # ------------------------------------------------------------------ 로그인

    def _check_throttle(self, *keys: str, limits: tuple[int, ...]) -> None:
        for key, limit in zip(keys, limits):
            if self.throttle.count(key) >= limit:
                minutes = (self.throttle.retry_after(key) + 59) // 60
                raise RateLimited(
                    f"로그인 시도가 너무 많습니다. {minutes}분 후 다시 시도해주세요."
                )

    async def login(
        self, phone: str, password: str, remember: bool = True, ip: str = ""
    ) -> tuple[str, SessionInfo]:
        if not is_valid_phone(phone):
            raise AuthFailed(
                "올바른 전화번호 형식을 입력해주세요. (010-xxxx-xxxx)",
                code="INVALID_PHONE",
            )
        user_id = normalize_phone(phone)
        phone_key, ip_key = f"phone:{user_id}", f"ip:{ip}"
        self._check_throttle(
            phone_key, ip_key, limits=(self.max_failures, IP_MAX_FAILURES)
        )

        if not self.users.is_allowed(user_id):
            self.throttle.fail(ip_key)
            if self.alert:
                await self.alert(
                    f"{format_phone(user_id)}는 등록되지 않은 사용자입니다. (웹)"
                )
            raise NotAllowed("등록되지 않은 사용자입니다. 관리자에게 문의하세요.")

        korail_id = format_phone(user_id)
        ok = await asyncio.to_thread(self.korail_login, korail_id, password)
        if not ok:
            failures = self.throttle.fail(phone_key)
            self.throttle.fail(ip_key)
            remaining = max(0, self.max_failures - failures)
            raise AuthFailed(
                "코레일 로그인에 실패했습니다. 비밀번호를 확인해주세요. "
                f"(남은 시도 {remaining}회, 코레일은 5회 실패 시 계정이 잠깁니다)"
            )

        self.throttle.reset(phone_key)
        self.users.touch_login(user_id)
        return self._create_session(user_id, False, korail_id, password, remember)

    async def admin_login(
        self, password: str, remember: bool = True, ip: str = ""
    ) -> tuple[str, SessionInfo]:
        ip_key = f"admin-ip:{ip}"
        self._check_throttle(ip_key, limits=(self.max_failures,))
        if not self.admin_password or not hmac.compare_digest(
            password.encode(), self.admin_password.encode()
        ):
            self.throttle.fail(ip_key)
            raise AuthFailed("관리자 비밀번호가 올바르지 않습니다.")
        if not (self.admin_korail_id and self.admin_korail_pw):
            raise ServiceError(
                "관리자 코레일 계정(ADMIN_KORAIL_ID/PW)이 설정되지 않았습니다.",
                code="ADMIN_NOT_CONFIGURED",
            )
        ok = await asyncio.to_thread(
            self.korail_login, self.admin_korail_id, self.admin_korail_pw
        )
        if not ok:
            raise AuthFailed("관리자 계정으로 코레일 로그인에 실패했습니다.")
        self.throttle.reset(ip_key)
        return self._create_session(
            ADMIN_USER_ID, True, self.admin_korail_id, self.admin_korail_pw, remember
        )

    def _create_session(
        self,
        user_id: str,
        is_admin: bool,
        korail_id: str,
        korail_pw: str,
        remember: bool,
    ) -> tuple[str, SessionInfo]:
        token = new_token()
        now = utcnow()
        ttl = self.session_ttl if remember else timedelta(hours=SHORT_SESSION_HOURS)
        model = WebSession(
            id=sha256_hex(token),
            user_id=user_id,
            is_admin=is_admin,
            korail_id=korail_id,
            korail_pw_enc=self.vault.encrypt(korail_pw),
            csrf_token=new_token(24),
            remember=remember,
            created_at=now,
            expires_at=now + ttl,
            last_seen_at=now,
        )
        with self.db.session() as s:
            s.add(model)
        return token, SessionInfo.from_model(model)

    # ------------------------------------------------------------------ 세션

    def resolve(self, token: Optional[str]) -> Optional[SessionInfo]:
        """쿠키 토큰으로 세션 조회 (만료/비활성 사용자는 None, 로그인 유지 세션은 연장)"""
        if not token:
            return None
        now = utcnow()
        with self.db.session() as s:
            m = s.get(WebSession, sha256_hex(token))
            if not m:
                return None
            if m.expires_at <= now:
                s.delete(m)
                return None
            if not m.is_admin:
                # 같은 세션에서 조회 (중첩 세션은 동시 요청 시 커넥션 풀을 고갈시킴)
                user = s.get(User, m.user_id)
                if not (user and user.is_active):
                    s.delete(m)
                    return None
            if m.remember and m.expires_at - now < self.session_ttl / 2:
                m.expires_at = now + self.session_ttl
            if now - m.last_seen_at > timedelta(minutes=5):
                m.last_seen_at = now
            return SessionInfo.from_model(m)

    def credentials(self, session: SessionInfo) -> tuple[str, str]:
        """예약 실행용 코레일 계정 (복호화 실패 시 재로그인 필요)"""
        with self.db.session() as s:
            m = s.get(WebSession, session.session_id)
            password = self.vault.decrypt(m.korail_pw_enc) if m else None
        if not password:
            raise AuthFailed(
                "세션이 만료되었습니다. 다시 로그인해주세요.", code="SESSION_EXPIRED"
            )
        return session.korail_id, password

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self.db.session() as s:
            s.execute(delete(WebSession).where(WebSession.id == sha256_hex(token)))

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.db.session() as s:
            s.execute(delete(WebSession).where(WebSession.user_id == user_id))

    def purge_expired(self) -> int:
        with self.db.session() as s:
            result = s.execute(
                delete(WebSession).where(WebSession.expires_at <= utcnow())
            )
            return result.rowcount or 0
