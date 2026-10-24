"""관리자: 사용자 관리"""

from typing import Optional, Union

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from core.app_settings import SettingOut
from core.errors import ValidationFailed
from core.schemas import UserOut

from .deps import AdminDep, ServicesDep

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserCreateIn(BaseModel):
    phone: str = Field(max_length=20)
    name: Optional[str] = Field(default=None, max_length=50)


class UserUpdateIn(BaseModel):
    """보낸 항목만 바꿈 (name은 빈 문자열이면 지움)"""

    name: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = None
    telegram_notify: Optional[bool] = None
    # false만 받음: 텔레그램 연결은 사용자가 봇에서 로그인해야 생김
    telegram_linked: Optional[bool] = None


@router.get("/users", response_model=list[UserOut])
def list_users(_: AdminDep, services: ServicesDep):
    return [UserOut.from_model(u) for u in services.users.list()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreateIn, _: AdminDep, services: ServicesDep):
    return UserOut.from_model(services.users.create(body.phone, body.name))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdateIn, _: AdminDep, services: ServicesDep):
    if body.telegram_linked is True:
        raise ValidationFailed(
            "텔레그램 연결은 사용자가 텔레그램 봇에서 로그인해야 합니다."
        )
    user = services.users.update(
        user_id,
        name=body.name,
        is_active=body.is_active,
        telegram_notify=body.telegram_notify,
        unlink_telegram=body.telegram_linked is False,
    )
    if body.is_active is False:
        services.auth.revoke_user_sessions(user.id)
    return UserOut.from_model(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, _: AdminDep, services: ServicesDep):
    services.users.delete(user_id)
    services.auth.revoke_user_sessions(user_id)
    return Response(status_code=204)


class SettingsUpdateIn(BaseModel):
    """바꿀 설정만 보냄. 값이 null 이면 기본값(환경변수)으로 되돌림"""

    values: dict[str, Optional[Union[int, float]]]


@router.get("/settings", response_model=list[SettingOut])
def list_settings(_: AdminDep, services: ServicesDep):
    return services.app_settings.describe()


@router.patch("/settings", response_model=list[SettingOut])
def update_settings(body: SettingsUpdateIn, _: AdminDep, services: ServicesDep):
    return services.app_settings.update(body.values)
