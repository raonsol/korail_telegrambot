"""Celery 예약 태스크의 Redis 상태 키 (웹 서버와 워커가 함께 사용)

- ``reservation_task:{id}`` (hash): ``status`` = running | completed | cancelled
  - running: 태스크가 시작됨 (HSETNX, 취소를 덮어쓰지 않음)
  - completed: 예약 성공, cancelled: 웹 서버가 취소 (워커가 매 시도 전에 확인하고 멈춤)
- ``reservation_hb:{id}`` (string, 만료 시간 있음): 워커 프로세스가 살아 있는 동안
  ``HEARTBEAT_INTERVAL_SECONDS``마다 갱신. 태스크가 끝나면 삭제
- ``egress_slot:{id}`` / ``egress_block:{id}`` / ``egress_block_level:{id}``: 코레일 출구별
  다음 요청 순서, 차단 대기(만료 시간 = 남은 대기), 연속 차단 단계 (``core.egress.RedisGate``)

웹 서버는 "시작됨(running)인데 heartbeat가 없는" 예약을 워커가 종료된 것으로 본다.
시간은 Redis 만료로 판단하므로 웹 서버와 워커의 시계가 달라도 된다.
이 모듈은 가볍게 유지한다 (워커와 웹 서버 모두 import).
"""

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

# 이 상태면 태스크를 시작하지 않거나 즉시 멈춤
STOP_STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED)

HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_TTL_SECONDS = 60

# 취소 표시 보관 시간 (재전달된 태스크도 시작하지 않도록 충분히 길게)
CANCEL_TTL_SECONDS = 7 * 24 * 3600


def state_key(reservation_id: str) -> str:
    return f"reservation_task:{reservation_id}"


def heartbeat_key(reservation_id: str) -> str:
    return f"reservation_hb:{reservation_id}"


def egress_slot_key(egress_id: str) -> str:
    return f"egress_slot:{egress_id}"


def egress_block_key(egress_id: str) -> str:
    return f"egress_block:{egress_id}"


def egress_level_key(egress_id: str) -> str:
    return f"egress_block_level:{egress_id}"
