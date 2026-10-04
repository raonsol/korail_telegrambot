"""ORM 모델"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class User(Base):
    """예약 서비스를 이용할 수 있는 사용자 (기존 ALLOW_LIST 대체)"""

    __tablename__ = "users"

    # 코레일 로그인 ID (core.schemas.account_key): 하이픈 없는 전화번호
    # 관리자 코레일 계정(ADMIN_KORAIL_ID)은 이메일·회원번호일 수 있음
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    telegram_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 코레일 접속에 쓰는 기기 신원 (pykorail 기기 프로파일 id + 합성 Android ID)
    # 처음 코레일 로그인할 때 발급해 계속 재사용. 여러 계정이 같은 Android ID를 쓰면 코레일이 차단함
    korail_device_profile: Mapped[Optional[str]] = mapped_column(String(40))
    korail_android_id: Mapped[Optional[str]] = mapped_column(
        String(16), unique=True, index=True
    )


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
    # 좌석 대신 예약대기를 신청해 성공한 경우 (결제 기한 없음, 좌석 배정 시 코레일이 알림)
    waitlisted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
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
