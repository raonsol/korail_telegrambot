"""사용자 관리 (DB)

기존 ``ALLOW_LIST`` 환경변수는 최초 실행 시 사용자 테이블을 채우는 용도로만 사용하며,
이후 사용자 추가/비활성화/삭제는 관리자 화면(웹) 또는 봇 관리자 명령으로 합니다.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .db import Database, utcnow
from .errors import Conflict, NotFound, ValidationFailed
from .models import PushSubscription, User
from .schemas import is_valid_phone, normalize_phone

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db: Database):
        self.db = db

    def seed_from_allow_list(self, allow_list: str) -> int:
        """ALLOW_LIST에 있는데 DB에 없는 번호만 추가 (기존 사용자는 건드리지 않음)"""
        phones = [normalize_phone(p) for p in (allow_list or "").split(",")]
        phones = [p for p in phones if is_valid_phone(p)]
        added = 0
        with self.db.session() as s:
            existing = set(s.scalars(select(User.id)))
            for phone in dict.fromkeys(phones):
                if phone not in existing:
                    s.add(User(id=phone, name=None, is_active=True))
                    added += 1
        if added:
            logger.info(f"Seeded {added} user(s) from ALLOW_LIST")
        return added

    def get(self, phone: str) -> Optional[User]:
        with self.db.session() as s:
            return s.get(User, normalize_phone(phone))

    def is_allowed(self, phone: str) -> bool:
        user = self.get(phone)
        return bool(user and user.is_active)

    def list(self) -> list[User]:
        with self.db.session() as s:
            return list(s.scalars(select(User).order_by(User.created_at, User.id)))

    def create(self, phone: str, name: Optional[str] = None) -> User:
        if not is_valid_phone(phone):
            raise ValidationFailed("올바른 전화번호 형식이 아닙니다. (010xxxxxxxx)")
        user = User(
            id=normalize_phone(phone),
            name=(name or "").strip() or None,
            is_active=True,
            created_at=utcnow(),
        )
        try:
            with self.db.session() as s:
                s.add(user)
        except IntegrityError:
            raise Conflict("이미 등록된 사용자입니다.")
        return user

    def update(
        self,
        phone: str,
        *,
        name: Optional[str] = None,
        is_active: Optional[bool] = None,
        telegram_notify: Optional[bool] = None,
    ) -> User:
        with self.db.session() as s:
            user = s.get(User, normalize_phone(phone))
            if not user:
                raise NotFound("사용자를 찾을 수 없습니다.")
            if name is not None:
                user.name = name.strip() or None
            if is_active is not None:
                user.is_active = is_active
            if telegram_notify is not None:
                user.telegram_notify = telegram_notify
            return user

    def delete(self, phone: str) -> None:
        with self.db.session() as s:
            user = s.get(User, normalize_phone(phone))
            if not user:
                raise NotFound("사용자를 찾을 수 없습니다.")
            # 푸시 구독은 외래키가 없어 직접 삭제 (남으면 같은 번호로 다시 등록된 사용자의
            # 알림이 이전 사용자의 브라우저로 전송됨)
            s.execute(
                delete(PushSubscription).where(PushSubscription.user_id == user.id)
            )
            s.delete(user)

    def link_telegram(self, phone: str, chat_id: int) -> None:
        """텔레그램에서 로그인한 사용자의 chat_id 연결 (웹 예약 결과를 텔레그램으로도 알림)"""
        with self.db.session() as s:
            user = s.get(User, normalize_phone(phone))
            if user:
                user.telegram_chat_id = chat_id
                user.last_login_at = utcnow()

    def unlink_telegram(self, phone: str) -> None:
        with self.db.session() as s:
            user = s.get(User, normalize_phone(phone))
            if user:
                user.telegram_chat_id = None

    def touch_login(self, phone: str) -> None:
        with self.db.session() as s:
            user = s.get(User, normalize_phone(phone))
            if user:
                user.last_login_at = utcnow()

    def telegram_target(self, phone: str) -> Optional[int]:
        """웹 예약 결과를 보낼 텔레그램 chat_id (알림을 켠 경우만)"""
        user = self.get(phone)
        if user and user.telegram_chat_id and user.telegram_notify:
            return user.telegram_chat_id
        return None

    def owner_for_chat(self, chat_id: int) -> Optional[str]:
        with self.db.session() as s:
            return s.scalar(
                select(User.id).where(User.telegram_chat_id == chat_id).limit(1)
            )
