"""관리자: 사용자 관리"""

from typing import Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from core.schemas import UserOut

from .deps import AdminDep, ServicesDep

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserCreateIn(BaseModel):
    phone: str = Field(max_length=20)
    name: Optional[str] = Field(default=None, max_length=50)


class UserUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = None


@router.get("/users", response_model=list[UserOut])
def list_users(_: AdminDep, services: ServicesDep):
    return [UserOut.from_model(u) for u in services.users.list()]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreateIn, _: AdminDep, services: ServicesDep):
    return UserOut.from_model(services.users.create(body.phone, body.name))


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdateIn, _: AdminDep, services: ServicesDep):
    user = services.users.update(user_id, name=body.name, is_active=body.is_active)
    if body.is_active is False:
        services.auth.revoke_user_sessions(user.id)
    return UserOut.from_model(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str, _: AdminDep, services: ServicesDep):
    services.users.delete(user_id)
    services.auth.revoke_user_sessions(user_id)
    return Response(status_code=204)
