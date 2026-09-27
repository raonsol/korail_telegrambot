"""로그인/로그아웃/내 정보"""

from typing import Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from core.auth import SessionInfo
from core.schemas import format_phone
from core.services import Services

from .deps import (
    COOKIE_NAME,
    ServicesDep,
    SessionDep,
    clear_session_cookie,
    client_ip,
    set_session_cookie,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    phone: str = Field(max_length=20)
    password: str = Field(min_length=1, max_length=100)
    remember: bool = True


class AdminLoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    remember: bool = True


class MeUser(BaseModel):
    id: str
    phone: Optional[str]
    name: Optional[str] = None
    is_admin: bool
    telegram_linked: bool = False
    telegram_notify: bool = False


class MeOut(BaseModel):
    user: MeUser
    csrf_token: str
    push_enabled: bool
    push_public_key: Optional[str] = None


class SettingsIn(BaseModel):
    telegram_notify: Optional[bool] = None


def build_me(services: Services, session: SessionInfo) -> MeOut:
    if session.is_admin:
        user = MeUser(id=session.user_id, phone=None, name="관리자", is_admin=True)
    else:
        u = services.users.get(session.user_id)
        user = MeUser(
            id=session.user_id,
            phone=format_phone(session.user_id),
            name=u.name if u else None,
            is_admin=False,
            telegram_linked=bool(u and u.telegram_chat_id),
            telegram_notify=bool(u and u.telegram_notify),
        )
    settings = services.settings
    return MeOut(
        user=user,
        csrf_token=session.csrf_token,
        push_enabled=settings.push_enabled,
        push_public_key=settings.vapid_public_key or None,
    )


@router.post("/login", response_model=MeOut)
async def login(
    body: LoginIn, request: Request, response: Response, services: ServicesDep
):
    token, session = await services.auth.login(
        body.phone, body.password, body.remember, client_ip(request)
    )
    set_session_cookie(response, token, session, services.settings.secure_cookies)
    return build_me(services, session)


@router.post("/admin-login", response_model=MeOut)
async def admin_login(
    body: AdminLoginIn, request: Request, response: Response, services: ServicesDep
):
    token, session = await services.auth.admin_login(
        body.password, body.remember, client_ip(request)
    )
    set_session_cookie(response, token, session, services.settings.secure_cookies)
    return build_me(services, session)


@router.post("/logout", status_code=204)
def logout(_: SessionDep, request: Request, services: ServicesDep):
    services.auth.logout(request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    clear_session_cookie(response, services.settings.secure_cookies)
    return response


@router.get("/me", response_model=MeOut)
def me(
    session: SessionDep, request: Request, response: Response, services: ServicesDep
):
    # 로그인 유지 세션은 서버에서 연장되므로 쿠키 만료도 함께 갱신
    token = request.cookies.get(COOKIE_NAME)
    if session.remember and token:
        set_session_cookie(response, token, session, services.settings.secure_cookies)
    return build_me(services, session)


@router.patch("/me/settings", response_model=MeOut)
def update_settings(body: SettingsIn, session: SessionDep, services: ServicesDep):
    if not session.is_admin and body.telegram_notify is not None:
        services.users.update(session.user_id, telegram_notify=body.telegram_notify)
    return build_me(services, session)
