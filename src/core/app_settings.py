"""관리자 화면에서 바꾸는 운영 설정 (예약 시도 간격·한도 등)

기본값은 환경변수(config.WebSettings)와 코드 기본값이고, 관리자가 바꾼 값만 DB(app_settings)에
저장한다. 바뀐 값은 웹 서버의 서비스 객체에 바로 반영되고(on_change), 워커는 DB를 쓰지 않으므로
예약을 시작할 때 spec 에 실려 간다 → 진행 중인 예약은 시작할 때의 값을 유지한다.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Union

from pydantic import BaseModel
from sqlalchemy import select

from .db import Database, utcnow
from .errors import ValidationFailed
from .models import AppSetting

logger = logging.getLogger(__name__)

Number = Union[int, float]


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    label: str
    unit: str
    help: str
    default: Number
    min: Number
    max: Number
    step: Number = 1  # 1이면 정수만

    @property
    def integer(self) -> bool:
        return self.step == 1


class SettingOut(BaseModel):
    key: str
    group: str
    label: str
    unit: str
    help: str
    value: Number
    default: Number
    min: Number
    max: Number
    step: Number
    # 관리자가 바꾼 값인지 (False면 기본값)
    overridden: bool


GROUP_ATTEMPT = "예약 시도"
GROUP_KORAIL = "코레일 요청"
GROUP_LIMIT = "예약 한도"


def setting_definitions(settings) -> list[SettingDef]:
    """설정 목록 (기본값은 환경변수에서)"""
    # Celery 워커는 환경변수 RESERVATION_TIMEOUT 보다 오래 돌 수 없음 (visibility_timeout 기준)
    # 분 단위로 보여주되 초 단위 환경변수를 넘지 않게 반영 (apply_settings 에서 다시 자름)
    max_minutes = max(1, math.ceil(int(settings.reservation_timeout) / 60))
    return [
        SettingDef(
            "attempt_interval",
            GROUP_ATTEMPT,
            "시도 간격",
            "초",
            "예약 1건이 조회 사이에 쉬는 시간. 실제 간격은 응답 시간과 출구별 분당 조회 수 한도 때문에 더 길 수 있습니다.",
            default=2.0,
            min=0.5,
            max=60,
            step=0.5,
        ),
        SettingDef(
            "max_attempts",
            GROUP_ATTEMPT,
            "최대 시도 횟수",
            "회",
            "이만큼 시도해도 표를 못 잡으면 예약을 끝냅니다.",
            default=1000,
            min=10,
            max=100_000,
        ),
        SettingDef(
            "max_duration_minutes",
            GROUP_ATTEMPT,
            "최대 실행 시간",
            "분",
            f"예약 1건이 시도를 계속하는 최대 시간. 환경변수 RESERVATION_TIMEOUT({max_minutes}분)보다 길게는 설정할 수 없습니다.",
            default=max_minutes,
            min=1,
            max=max_minutes,
        ),
        SettingDef(
            "relogin_after_errors",
            GROUP_ATTEMPT,
            "재로그인 기준",
            "회",
            "예상 밖의 오류가 연속으로 이만큼 오면 코레일에 다시 로그인합니다. 세션 만료 오류는 바로 다시 로그인합니다.",
            default=10,
            min=1,
            max=100,
        ),
        SettingDef(
            "same_error_limit",
            GROUP_ATTEMPT,
            "같은 오류 중단 기준",
            "회",
            "열차 없음·매진·차단이 아닌 같은 오류가 연속으로 이만큼 오면 예약을 오류로 끝냅니다.",
            default=10,
            min=1,
            max=1000,
        ),
        SettingDef(
            "progress_every",
            GROUP_ATTEMPT,
            "진행 보고 주기",
            "회",
            "시도 횟수를 이만큼마다 화면에 반영합니다. 1이면 매 시도.",
            default=1,
            min=1,
            max=100,
        ),
        SettingDef(
            "korail_egress_rpm",
            GROUP_KORAIL,
            "출구별 분당 조회 수",
            "회/분",
            "같은 출구(IP)를 쓰는 모든 예약과 열차 목록 조회를 합친 한도. 0이면 제한 없음. 너무 높으면 코레일이 매크로로 차단할 수 있습니다.",
            default=int(settings.korail_egress_rpm),
            min=0,
            max=600,
        ),
        SettingDef(
            "korail_egress_max_active",
            GROUP_KORAIL,
            "출구별 동시 예약",
            "개",
            "한 출구로 동시에 진행할 수 있는 예약 수. 0이면 제한 없음. 관리자에게도 적용됩니다.",
            default=int(settings.korail_egress_max_active),
            min=0,
            max=1000,
        ),
        SettingDef(
            "max_concurrent_reservations",
            GROUP_LIMIT,
            "전체 동시 예약",
            "개",
            "서버 전체에서 동시에 진행할 수 있는 예약 수. Celery 모드에서 워커 슬롯(CELERY_CONCURRENCY)보다 크면 나머지는 대기열에서 기다립니다.",
            default=int(settings.max_concurrent_reservations),
            min=1,
            max=1000,
        ),
        SettingDef(
            "max_reservations_per_user",
            GROUP_LIMIT,
            "사용자별 동시 예약",
            "개",
            "사용자 한 명이 동시에 진행할 수 있는 예약 수 (관리자는 제외).",
            default=int(settings.max_reservations_per_user),
            min=1,
            max=100,
        ),
    ]


class AppSettingsService:
    def __init__(
        self,
        db: Database,
        definitions: list[SettingDef],
        on_change: Callable[[dict[str, Number]], None] = lambda _: None,
    ):
        self.db = db
        self.definitions = {d.key: d for d in definitions}
        self._on_change = on_change
        self._overrides: dict[str, Number] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        """DB에 저장된 값을 읽어 반영 (마이그레이션 후 호출). 범위를 벗어난 값은 무시"""
        with self.db.session() as s:
            rows = list(s.scalars(select(AppSetting)))
        overrides = {}
        for row in rows:
            definition = self.definitions.get(row.key)
            if definition is None:
                continue
            try:
                overrides[row.key] = _check(definition, json.loads(row.value))
            except (ValueError, ValidationFailed) as e:
                logger.warning(f"Ignoring stored setting {row.key}={row.value!r}: {e}")
        with self._lock:
            self._overrides = overrides
        if overrides:
            logger.info(f"Admin settings: {overrides}")
        self._on_change(self.values())

    def values(self) -> dict[str, Number]:
        with self._lock:
            overrides = dict(self._overrides)
        return {k: overrides.get(k, d.default) for k, d in self.definitions.items()}

    def describe(self) -> list[SettingOut]:
        values = self.values()
        with self._lock:
            overridden = set(self._overrides)
        return [
            SettingOut(
                key=d.key,
                group=d.group,
                label=d.label,
                unit=d.unit,
                help=d.help,
                value=values[d.key],
                default=d.default,
                min=d.min,
                max=d.max,
                step=d.step,
                overridden=d.key in overridden,
            )
            for d in self.definitions.values()
        ]

    def update(self, changes: dict[str, Optional[Number]]) -> list[SettingOut]:
        """값 바꾸기. None 이면 기본값으로 되돌림. 하나라도 잘못되면 아무것도 바꾸지 않음"""
        checked: dict[str, Optional[Number]] = {}
        for key, value in changes.items():
            definition = self.definitions.get(key)
            if definition is None:
                raise ValidationFailed(f"알 수 없는 설정입니다: {key}")
            checked[key] = None if value is None else _check(definition, value)

        now = utcnow()
        with self.db.session() as s:
            for key, value in checked.items():
                row = s.get(AppSetting, key)
                if value is None:
                    if row is not None:
                        s.delete(row)
                elif row is None:
                    s.add(AppSetting(key=key, value=json.dumps(value), updated_at=now))
                else:
                    row.value = json.dumps(value)
                    row.updated_at = now
        with self._lock:
            for key, value in checked.items():
                if value is None:
                    self._overrides.pop(key, None)
                else:
                    self._overrides[key] = value
        logger.info(f"Admin settings changed: {checked}")
        self._on_change(self.values())
        return self.describe()


def _check(definition: SettingDef, value) -> Number:
    label = definition.label
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationFailed(f"{label}: 숫자를 입력해주세요.")
    if definition.integer:
        if value != int(value):
            raise ValidationFailed(f"{label}: 정수를 입력해주세요.")
        value = int(value)
    else:
        steps = value / definition.step
        if abs(steps - round(steps)) > 1e-9:
            raise ValidationFailed(f"{label}: {definition.step} 단위로 입력해주세요.")
        value = float(value)
    if not definition.min <= value <= definition.max:
        raise ValidationFailed(
            f"{label}: {definition.min}~{definition.max} {definition.unit} 사이로 입력해주세요."
        )
    return value
