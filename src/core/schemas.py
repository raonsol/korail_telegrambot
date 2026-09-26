"""API/서비스 계층에서 사용하는 Pydantic 스키마"""

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # tzdata가 없는 환경
    KST = timezone(timedelta(hours=9))

# 예약 가능한 최대 날짜 범위 (코레일은 약 1개월 전부터 예매 가능)
MAX_DAYS_AHEAD = 90

TRAIN_TYPE_LABELS = {"KTX": "KTX", "ALL": "모든 열차"}
SEAT_TYPE_LABELS = {
    "general": "일반실 우선 예약",
    "general_only": "일반실만 예약",
    "special": "특실 우선 예약",
    "special_only": "특실만 예약",
}

TrainTypeKey = Literal["KTX", "ALL"]
SeatTypeKey = Literal["general", "general_only", "special", "special_only"]


def now_kst() -> datetime:
    return datetime.now(KST)


def normalize_phone(phone: str) -> str:
    """전화번호에서 하이픈/공백 제거"""
    return "".join(ch for ch in str(phone) if ch.isdigit())


def is_valid_phone(phone: str) -> bool:
    digits = normalize_phone(phone)
    return len(digits) == 11 and digits.startswith("010")


def format_phone(phone: str) -> str:
    """01012345678 -> 010-1234-5678 (코레일 로그인 형식)"""
    d = normalize_phone(phone)
    if len(d) != 11:
        return phone
    return f"{d[:3]}-{d[3:7]}-{d[7:]}"


def _validate_hhmm(value: str) -> str:
    value = str(value)
    if not (len(value) == 4 and value.isdigit()):
        raise ValueError("시간은 HHMM 형식이어야 합니다.")
    hours, minutes = int(value[:2]), int(value[2:])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError("올바르지 않은 시간입니다.")
    return value


class ReservationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (ReservationStatus.QUEUED.value, ReservationStatus.RUNNING.value)
TERMINAL_STATUSES = (
    ReservationStatus.SUCCESS.value,
    ReservationStatus.FAILED.value,
    ReservationStatus.ERROR.value,
    ReservationStatus.CANCELLED.value,
)


class ReservationRequest(BaseModel):
    """예약 요청 (웹 폼과 텔레그램 대화 결과 모두 이 형태로 변환)"""

    dep_date: date
    src_station: str = Field(min_length=1, max_length=30)
    dst_station: str = Field(min_length=1, max_length=30)
    dep_time: str  # HHMM
    max_dep_time: str  # HHMM
    train_type: TrainTypeKey = "KTX"
    seat_type: SeatTypeKey = "general"

    @field_validator("dep_date", mode="before")
    @classmethod
    def _parse_compact_date(cls, value):
        if isinstance(value, str) and len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").date()
        return value

    @field_validator("src_station", "dst_station")
    @classmethod
    def _strip_station(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("역 이름을 입력해주세요.")
        return value

    @field_validator("dep_time", "max_dep_time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        return _validate_hhmm(value)

    @model_validator(mode="after")
    def _check_consistency(self):
        now = now_kst()
        today = now.date()
        if self.dep_date < today:
            raise ValueError("출발일이 오늘보다 이전입니다.")
        if self.dep_date > today + timedelta(days=MAX_DAYS_AHEAD):
            raise ValueError("출발일이 너무 먼 미래입니다.")
        if self.src_station == self.dst_station:
            raise ValueError("출발역과 도착역이 같습니다.")
        if self.dep_date == today and self.dep_time < now.strftime("%H%M"):
            raise ValueError("출발 시각이 현재 시각보다 이전입니다.")
        if int(self.max_dep_time) < int(self.dep_time):
            raise ValueError("최대 출발 시각이 출발 시각보다 이전입니다.")
        return self

    @property
    def dep_date_compact(self) -> str:
        return self.dep_date.strftime("%Y%m%d")


class ReservationOut(BaseModel):
    id: str
    owner_id: str
    origin: str
    status: ReservationStatus
    dep_date: str
    src_station: str
    dst_station: str
    dep_time: str
    max_dep_time: str
    train_type: str
    seat_type: str
    train_type_label: str
    seat_type_label: str
    attempts: int
    result_text: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None
    is_active: bool

    @field_serializer("created_at", "updated_at", "finished_at")
    def _as_utc(self, value: Optional[datetime]):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def from_model(cls, r) -> "ReservationOut":
        return cls(
            id=r.id,
            owner_id=r.owner_id,
            origin=r.origin,
            status=r.status,
            dep_date=r.dep_date,
            src_station=r.src_station,
            dst_station=r.dst_station,
            dep_time=r.dep_time,
            max_dep_time=r.max_dep_time,
            train_type=r.train_type,
            seat_type=r.seat_type,
            train_type_label=TRAIN_TYPE_LABELS.get(r.train_type, r.train_type),
            seat_type_label=SEAT_TYPE_LABELS.get(r.seat_type, r.seat_type),
            attempts=r.attempts or 0,
            result_text=r.result_text,
            error=r.error,
            created_at=r.created_at,
            updated_at=r.updated_at,
            finished_at=r.finished_at,
            is_active=r.status in ACTIVE_STATUSES,
        )


class WorkerEvent(BaseModel):
    """워커(subprocess/Celery)가 보내는 상태 보고"""

    reservation_id: str
    token: str
    status: Literal["running", "progress", "success", "failed", "error"]
    attempts: Optional[int] = None
    message: Optional[str] = None
    train_info: Optional[str] = None


class Owner(BaseModel):
    """예약 소유자 (전화번호 또는 관리자)"""

    user_id: str
    is_admin: bool = False


ADMIN_USER_ID = "admin"


class UserOut(BaseModel):
    id: str
    phone: str
    name: Optional[str] = None
    is_active: bool
    telegram_linked: bool
    telegram_notify: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    @field_serializer("created_at", "last_login_at")
    def _as_utc(self, value: Optional[datetime]):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @classmethod
    def from_model(cls, u) -> "UserOut":
        return cls(
            id=u.id,
            phone=format_phone(u.id),
            name=u.name,
            is_active=u.is_active,
            telegram_linked=u.telegram_chat_id is not None,
            telegram_notify=u.telegram_notify,
            created_at=u.created_at,
            last_login_at=u.last_login_at,
        )
