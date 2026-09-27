"""웹 API 공통 의존성: 세션 쿠키 인증, CSRF 검증"""

import hmac
from typing import Annotated

from fastapi import Depends, Request, Response

from core.auth import SessionInfo
from core.db import utcnow
from core.errors import AuthFailed, NotAllowed
from core.services import Services

COOKIE_NAME = "korail_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def client_ip(request: Request) -> str:
    """로그인 제한에 쓰는 클라이언트 IP

    X-Forwarded-For를 직접 읽지 않는다 (누구나 보낼 수 있어 매 요청 다른 값으로 제한을 우회함).
    리버스 프록시 뒤라면 uvicorn(fastapi run/dev의 기본 proxy headers)이 신뢰하는 프록시
    (``FORWARDED_ALLOW_IPS``, 기본 127.0.0.1)에서 온 요청에 한해 실제 클라이언트 주소로 바꿔 둔다.
    """
    return request.client.host if request.client else ""


def current_session(request: Request, services: ServicesDep) -> SessionInfo:
    session = services.auth.resolve(request.cookies.get(COOKIE_NAME))
    if not session:
        raise AuthFailed("로그인이 필요합니다.", code="UNAUTHENTICATED")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(header.encode(), session.csrf_token.encode()):
            raise NotAllowed("잘못된 요청입니다. (CSRF)", code="CSRF")
    return session


SessionDep = Annotated[SessionInfo, Depends(current_session)]


def admin_session(session: SessionDep) -> SessionInfo:
    if not session.is_admin:
        raise NotAllowed("관리자만 사용할 수 있습니다.")
    return session


AdminDep = Annotated[SessionInfo, Depends(admin_session)]


def set_session_cookie(
    response: Response, token: str, session: SessionInfo, secure: bool
) -> None:
    kwargs = {}
    if session.remember:
        # "로그인 유지"일 때만 영구 쿠키, 아니면 브라우저 세션 쿠키
        kwargs["max_age"] = max(0, int((session.expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        **kwargs,
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=secure, samesite="lax"
    )
