"""사용자 관리 (DB)

기존 ``ALLOW_LIST`` 환경변수는 최초 실행 시 사용자 테이블을 채우는 용도로만 사용하며,
이후 사용자 추가/비활성화/삭제는 관리자 화면(웹) 또는 봇 관리자 명령으로 합니다.
"""

from __future__ import annotations

import logging
from typing import Optional

from pykorail.device import random_profile
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .db import Database, utcnow
from .errors import Conflict, NotFound, ValidationFailed
from .models import PushSubscription, User
from .schemas import PHONE_FORMAT_HINT, account_key, is_valid_phone, normalize_phone

logger = logging.getLogger(__name__)


# ADMIN_KORAIL_ID 로 자동 추가한 사용자 행의 이름
ADMIN_ACCOUNT_NAME = "관리자 코레일 계정"


class UserService:
    def __init__(self, db: Database, admin_korail_id: Optional[str] = None):
        self.db = db
        # 관리자 코레일 계정 - 기기 신원을 저장하도록 사용자 행을 둠 (ensure_admin_account)
        self.admin_key = account_key(admin_korail_id) if admin_korail_id else ""

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
            return s.get(User, account_key(phone))

    def is_allowed(self, phone: str) -> bool:
        user = self.get(phone)
        return bool(user and user.is_active)

    def list(self) -> list[User]:
        with self.db.session() as s:
            return list(s.scalars(select(User).order_by(User.created_at, User.id)))

    def create(self, phone: str, name: Optional[str] = None) -> User:
        if not is_valid_phone(phone):
            raise ValidationFailed(
                f"올바른 전화번호 형식이 아닙니다. ({PHONE_FORMAT_HINT})"
            )
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
            user = s.get(User, account_key(phone))
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
            user = s.get(User, account_key(phone))
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
            user = s.get(User, account_key(phone))
            if user:
                user.telegram_chat_id = chat_id
                user.last_login_at = utcnow()

    def unlink_telegram(self, phone: str) -> None:
        with self.db.session() as s:
            user = s.get(User, account_key(phone))
            if user:
                user.telegram_chat_id = None

    def touch_login(self, phone: str) -> None:
        with self.db.session() as s:
            user = s.get(User, account_key(phone))
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

    # ------------------------------------------------------------ 관리자 계정

    def ensure_admin_account(self) -> bool:
        """ADMIN_KORAIL_ID 사용자 행이 없으면 추가 (기기 신원 저장용)

        로그인 권한은 바꾸지 않도록 비활성으로 추가한다 (이미 있는 행은 그대로).
        """
        if not self.admin_key:
            return False
        try:
            with self.db.session() as s:
                if s.get(User, self.admin_key):
                    return False
                s.add(
                    User(
                        id=self.admin_key,
                        name=ADMIN_ACCOUNT_NAME,
                        is_active=False,
                        created_at=utcnow(),
                    )
                )
        except IntegrityError:
            return False  # 동시에 다른 요청이 추가함
        logger.info("Added the admin Korail account to the user table")
        return True

    # ------------------------------------------------------------ 코레일 기기

    def korail_device(self, korail_id: str) -> Optional[dict]:
        """코레일 계정이 접속할 때 쓰는 기기 신원 ``{"profile_id", "android_id"}``

        실제 앱처럼 계정마다 같은 기기로 보이도록 사용자 행에 한 번 발급해 계속 재사용한다
        (예약 워커에는 spec["korail_device"]로 전달). 여러 계정이 같은 Android ID를 쓰면
        코레일이 차단하므로 계정마다 따로 발급한다. 관리자 코레일 계정은 행이 없으면
        (삭제된 경우 포함) 추가한다. 사용자 행이 없는 계정은 None (pykorail 이 새 ID 생성).
        """
        key = account_key(korail_id)
        if key and key == self.admin_key:
            self.ensure_admin_account()
        device = self._user_device(key) if key else None
        if device is None:
            logger.warning("No user row for a Korail account; using a one-off device")
        return device

    def _user_device(self, user_id: str) -> Optional[dict]:
        for _ in range(3):
            profile = random_profile()
            try:
                with self.db.session() as s:
                    # 동시에 처음 로그인해도 먼저 저장된 값 하나만 쓰도록 비어 있을 때만 기록
                    s.execute(
                        update(User)
                        .where(User.id == user_id, User.korail_android_id.is_(None))
                        .values(
                            korail_device_profile=profile.id,
                            korail_android_id=profile.android_id,
                        )
                    )
                    row = s.execute(
                        select(
                            User.korail_device_profile, User.korail_android_id
                        ).where(User.id == user_id)
                    ).first()
            except IntegrityError:
                continue  # 다른 사용자와 Android ID가 겹침 (사실상 없음) - 새로 발급
            if row is None:
                return None
            return {"profile_id": row[0], "android_id": row[1]}
        raise RuntimeError("코레일 기기 ID를 발급하지 못했습니다.")
