"""코레일 요청 출구(egress) 관리

코레일 요청을 여러 출구(프록시 또는 이 서버의 직접 연결)로 나눠 보낸다.

- 계정 고정: 같은 코레일 계정은 항상 같은 출구를 쓴다 (``EgressPool.for_account``).
  한 계정이 여러 IP에서 보이면 그 자체로 매크로 신호이므로, 출구가 차단돼도
  다른 출구로 옮기지 않고 차단이 풀릴 때까지 기다린다.
- 출구별 요청 간격: ``rpm``(분당 검색 횟수)을 넘지 않도록 출구 단위로 순서를 정한다.
- 출구별 차단 대기: 코레일 차단 응답(-2000)을 받으면 그 출구를 쓰는 모든 예약이
  일정 시간(5분 → 15분 → 30분 → 60분) 요청을 멈춘다. 같은 차단을 여러 예약이
  동시에 보고해도 대기 시간은 한 번만 늘어난다.

출구 상태(다음 요청 시각, 차단 해제 시각)는 실행 모드에 따라 공유한다.
- subprocess: 같은 서버의 파일 (``FileGate``, 파일 잠금)
- Celery: Redis (``RedisGate``, 여러 워커 서버가 공유)

이 모듈은 워커도 import하므로 DB/웹 계층을 import하지 않는다.
프록시 주소에는 인증 정보가 들어갈 수 있으므로 로그에는 출구 ID만 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DIRECT = "direct"

# 코레일 차단 응답 후 출구를 쉬게 하는 시간 (연속 차단마다 다음 단계, 마지막 값에서 유지)
BLOCK_BACKOFF_SECONDS = (300, 900, 1800, 3600)
# 마지막 차단 후 이 시간이 지나면 대기 단계를 처음부터 다시 시작
BLOCK_LEVEL_RESET_SECONDS = 2 * 3600

PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
_EGRESS_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


@dataclass(frozen=True)
class Egress:
    id: str
    # 비어 있으면 이 서버에서 직접 요청
    proxy_url: str = ""

    @property
    def is_direct(self) -> bool:
        return not self.proxy_url


def parse_egresses(value: str) -> list[Egress]:
    """``KORAIL_EGRESSES`` 해석

    쉼표로 구분한 ``ID=프록시 URL`` 목록. ``ID`` 만 쓰면 이 서버의 직접 연결.
    비어 있으면 직접 연결 하나(``direct``) - 기존 동작과 같음.

        KORAIL_EGRESSES=home1=socks5h://100.64.0.2:1080,home2=socks5h://100.64.0.3:1080
        KORAIL_EGRESSES=direct,home1=socks5h://user:pass@100.64.0.2:1080

    Raises:
        ValueError: 형식 오류 (메시지에 프록시 주소는 넣지 않음)
    """
    value = (value or "").strip().strip("\"'").strip()
    if not value:
        return [Egress(DIRECT)]

    egresses: list[Egress] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        egress_id, _, proxy_url = item.partition("=")
        egress_id, proxy_url = egress_id.strip(), proxy_url.strip()
        if not _EGRESS_ID.match(egress_id):
            raise ValueError(
                f"KORAIL_EGRESSES: 출구 ID는 영문/숫자/-/_ 32자 이하여야 합니다: {egress_id!r}"
            )
        if proxy_url:
            parts = urlsplit(proxy_url)
            if parts.scheme not in PROXY_SCHEMES or not parts.hostname:
                raise ValueError(
                    f"KORAIL_EGRESSES: 출구 {egress_id}의 프록시 주소가 올바르지 않습니다 "
                    f"({'/'.join(PROXY_SCHEMES)}://호스트:포트)"
                )
        if any(e.id == egress_id for e in egresses):
            raise ValueError(f"KORAIL_EGRESSES: 출구 ID가 중복됩니다: {egress_id}")
        egresses.append(Egress(egress_id, proxy_url))
    if not egresses:
        return [Egress(DIRECT)]
    return egresses


def assign_egress(account: str, egresses: list[Egress]) -> Egress:
    """계정에 고정된 출구 (rendezvous hashing)

    출구를 추가·삭제해도 그 출구에 배정됐던 계정만 옮겨지고 나머지 계정의 출구는 그대로다.
    """
    if not egresses:
        raise ValueError("출구가 없습니다.")
    key = (account or "").replace("-", "")

    def score(egress: Egress) -> bytes:
        return hashlib.sha256(f"{egress.id}\0{key}".encode()).digest()

    return max(egresses, key=score)


# ---------------------------------------------------------------------- 출구 상태


class EgressGate(Protocol):
    """한 출구의 요청 간격과 차단 대기 상태 (여러 예약이 공유)"""

    def reserve_slot(self) -> float:
        """다음 요청 순서를 잡고, 그 순서까지 기다려야 하는 시간(초)을 돌려줌"""
        ...

    def blocked_for(self) -> float:
        """차단 대기가 남은 시간(초). 대기 중이 아니면 0"""
        ...

    def report_block(self) -> float:
        """코레일 차단 응답을 받았음을 기록하고 남은 대기 시간(초)을 돌려줌

        이미 대기 중이면(다른 예약이 먼저 보고) 대기 시간을 늘리지 않는다.
        """
        ...


def backoff_seconds(level: int) -> int:
    """연속 차단 횟수(1부터)에 따른 대기 시간"""
    index = min(max(level, 1), len(BLOCK_BACKOFF_SECONDS)) - 1
    return BLOCK_BACKOFF_SECONDS[index]


def _interval(rpm: int) -> float:
    return 60.0 / rpm if rpm and rpm > 0 else 0.0


def _reserve(state: dict, now: float, rpm: int) -> float:
    interval = _interval(rpm)
    if not interval:
        return 0.0
    slot = max(now, float(state.get("next_slot") or 0.0))
    state["next_slot"] = slot + interval
    return slot - now


def _blocked_for(state: dict, now: float) -> float:
    return max(0.0, float(state.get("blocked_until") or 0.0) - now)


def _report_block(state: dict, now: float) -> float:
    remaining = _blocked_for(state, now)
    if remaining > 0:
        return remaining
    last = float(state.get("last_block") or 0.0)
    level = int(state.get("level") or 0)
    level = level + 1 if now - last < BLOCK_LEVEL_RESET_SECONDS else 1
    pause = backoff_seconds(level)
    state.update(level=level, last_block=now, blocked_until=now + pause)
    return float(pause)


class MemoryGate:
    """프로세스 안에서만 공유하는 출구 상태 (테스트, Redis 없는 Celery 워커)"""

    def __init__(self, rpm: int = 0, clock: Callable[[], float] = time.time):
        self.rpm = rpm
        self.clock = clock
        self._state: dict = {}
        self._lock = threading.Lock()

    def reserve_slot(self) -> float:
        with self._lock:
            return _reserve(self._state, self.clock(), self.rpm)

    def blocked_for(self) -> float:
        with self._lock:
            return _blocked_for(self._state, self.clock())

    def report_block(self) -> float:
        with self._lock:
            return _report_block(self._state, self.clock())


class FileGate:
    """같은 서버의 여러 프로세스가 공유하는 출구 상태 (subprocess 모드)

    상태는 JSON 파일 하나이며 읽고 쓰는 동안 파일 잠금(fcntl)을 건다.
    파일을 읽거나 쓰지 못하면 제한 없이 진행한다 (예약을 멈추지 않음).
    """

    def __init__(self, path: str, rpm: int = 0, clock: Callable[[], float] = time.time):
        self.path = path
        self.rpm = rpm
        self.clock = clock

    def _update(self, change: Callable[[dict, float], float]) -> float:
        import fcntl

        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            logger.warning(f"Egress state unavailable ({self.path}): {e}")
            return 0.0
        try:
            with os.fdopen(fd, "r+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                raw = f.read()
                try:
                    state = json.loads(raw) if raw else {}
                except ValueError:
                    state = {}
                before = dict(state)
                result = change(state, self.clock())
                if state != before:
                    f.seek(0)
                    f.truncate()
                    json.dump(state, f)
                    f.flush()
                return result
        except OSError as e:
            logger.warning(f"Egress state unavailable ({self.path}): {e}")
            return 0.0

    def reserve_slot(self) -> float:
        return self._update(lambda state, now: _reserve(state, now, self.rpm))

    def blocked_for(self) -> float:
        return self._update(_blocked_for)

    def report_block(self) -> float:
        return self._update(_report_block)


def file_gate_path(directory: str, egress_id: str) -> str:
    return os.path.join(directory, f"egress_{egress_id}.json")


class RedisGate:
    """Redis로 공유하는 출구 상태 (Celery 모드, 여러 워커 서버)

    시각은 Redis 서버 시계(TIME)를 기준으로 해 워커 서버끼리 시계가 달라도 된다.
    Redis 오류 시에는 제한 없이 진행한다 (예약을 멈추지 않음).
    """

    def __init__(self, client, egress_id: str, rpm: int = 0):
        from .task_state import egress_block_key, egress_level_key, egress_slot_key

        self.client = client
        self.rpm = rpm
        self.slot_key = egress_slot_key(egress_id)
        self.block_key = egress_block_key(egress_id)
        self.level_key = egress_level_key(egress_id)

    def _now_ms(self, client) -> int:
        seconds, micros = client.time()
        return int(seconds) * 1000 + int(micros) // 1000

    def reserve_slot(self) -> float:
        interval_ms = int(_interval(self.rpm) * 1000)
        if not interval_ms:
            return 0.0
        from redis.exceptions import WatchError

        try:
            for _ in range(20):
                with self.client.pipeline() as pipe:
                    try:
                        pipe.watch(self.slot_key)
                        now = self._now_ms(pipe)
                        slot = max(now, int(pipe.get(self.slot_key) or 0))
                        pipe.multi()
                        # 다음 순서 + 여유 시간이 지나면 키가 사라짐 (오래 쉬던 출구)
                        pipe.set(
                            self.slot_key,
                            slot + interval_ms,
                            px=slot - now + interval_ms + 60_000,
                        )
                        pipe.execute()
                        return (slot - now) / 1000
                    except WatchError:
                        continue
        except Exception as e:
            logger.warning(f"Egress pacing unavailable: {e}")
            return 0.0
        # 경합이 계속되면 한 간격만큼 쉬고 진행
        return interval_ms / 1000

    def blocked_for(self) -> float:
        try:
            remaining = self.client.pttl(self.block_key)
        except Exception as e:
            logger.warning(f"Egress block state unavailable: {e}")
            return 0.0
        return remaining / 1000 if remaining and remaining > 0 else 0.0

    def report_block(self) -> float:
        try:
            # 먼저 차단 표시를 잡은 예약만 대기 단계를 올림 (동시에 보고해도 한 번만)
            first = backoff_seconds(1)
            if not self.client.set(self.block_key, "1", nx=True, ex=first):
                return self.blocked_for() or float(first)
            level = int(self.client.incr(self.level_key))
            pause = backoff_seconds(level)
            pipe = self.client.pipeline()
            # 차단 시작부터 재는 MemoryGate/FileGate와 같은 기준 (대기 시간은 더하지 않음)
            pipe.expire(self.level_key, BLOCK_LEVEL_RESET_SECONDS)
            pipe.expire(self.block_key, pause)
            pipe.execute()
            return float(pause)
        except Exception as e:
            logger.warning(f"Failed to record egress block: {e}")
            return float(backoff_seconds(1))


class NullGate:
    """제한 없음"""

    def reserve_slot(self) -> float:
        return 0.0

    def blocked_for(self) -> float:
        return 0.0

    def report_block(self) -> float:
        return 0.0


# ---------------------------------------------------------------------- 출구 목록


class EgressPool:
    """설정된 출구 목록과 출구별 상태 (웹 서버)"""

    def __init__(
        self,
        egresses: list[Egress],
        rpm: int = 0,
        max_active: int = 0,
        gate_factory: Optional[Callable[[str], EgressGate]] = None,
    ):
        if not egresses:
            raise ValueError("출구가 없습니다.")
        self.egresses = list(egresses)
        # 출구별 분당 검색 횟수 (0이면 제한 없음)
        self.rpm = rpm
        # 출구별 동시 예약 수 (0이면 제한 없음)
        self.max_active = max_active
        self._gate_factory = gate_factory
        self._gates: dict[str, EgressGate] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings, gate_factory=None) -> "EgressPool":
        return cls(
            parse_egresses(getattr(settings, "korail_egresses", "")),
            rpm=getattr(settings, "korail_egress_rpm", 0),
            max_active=getattr(settings, "korail_egress_max_active", 0),
            gate_factory=gate_factory,
        )

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.egresses]

    def for_account(self, korail_id: str) -> Egress:
        return assign_egress(korail_id, self.egresses)

    def gate(self, egress_id: str) -> EgressGate:
        with self._lock:
            gate = self._gates.get(egress_id)
            if gate is None:
                gate = (
                    self._gate_factory(egress_id)
                    if self._gate_factory
                    else MemoryGate(self.rpm)
                )
                self._gates[egress_id] = gate
            return gate

    def describe(self) -> str:
        """시작 로그용 (프록시 주소는 숨김)"""
        kinds = [
            f"{e.id}({'direct' if e.is_direct else 'proxy'})" for e in self.egresses
        ]
        limits = f"rpm={self.rpm or 'unlimited'}, max_active={self.max_active or 'unlimited'}"
        return f"{', '.join(kinds)} [{limits}]"
