"""ORM 모델"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class User(Base):
    """예약 서비스를 이용할 수 있는 사용자 (기존 ALLOW_LIST 대체)"""

    __tablename__ = "users"

    # 하이픈 없는 전화번호 (코레일 로그인 ID)
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    telegram_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class WebSession(Base):
    """웹 로그인 세션 (쿠키에는 토큰 원문, DB에는 해시만 저장)"""

    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token)
    user_id: Mapped[str] = mapped_column(String(20), index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    korail_id: Mapped[str] = mapped_column(String(50))
    korail_pw_enc: Mapped[str] = mapped_column(Text)
    csrf_token: Mapped[str] = mapped_column(String(64))
    remember: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reservation(Base):
    """예약 작업 (진행 중 + 30일 이력)"""

    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(20), index=True)
    origin: Mapped[str] = mapped_column(String(10))  # telegram | web
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    korail_id: Mapped[str] = mapped_column(String(50))

    dep_date: Mapped[str] = mapped_column(String(8))  # YYYYMMDD
    src_station: Mapped[str] = mapped_column(String(30))
    dst_station: Mapped[str] = mapped_column(String(30))
    dep_time: Mapped[str] = mapped_column(String(4))  # HHMM
    max_dep_time: Mapped[str] = mapped_column(String(4))  # HHMM
    train_type: Mapped[str] = mapped_column(String(10))
    seat_type: Mapped[str] = mapped_column(String(20))

    status: Mapped[str] = mapped_column(String(12), index=True)
    runner: Mapped[str] = mapped_column(String(12))  # subprocess | celery
    runner_ref: Mapped[Optional[str]] = mapped_column(String(64))
    callback_token_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_text: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class PushSubscription(Base):
    """Web Push 구독 정보"""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(20), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
