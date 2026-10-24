"""만료 시간과 최대 항목 수가 있는 작은 메모리 캐시 (열차 시간표, 내 승차권)"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, Hashable, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """스레드 안전. 넘치면 만료된 항목, 그다음 가장 오래 넣은 항목부터 버림"""

    def __init__(
        self,
        ttl: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._items: dict[K, tuple[float, V]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> Optional[V]:
        with self._lock:
            hit = self._items.get(key)
        if hit and self._clock() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key: K, value: V) -> None:
        now = self._clock()
        with self._lock:
            for k in [k for k, (t, _) in self._items.items() if now - t >= self.ttl]:
                del self._items[k]
            # 같은 키는 지우고 다시 넣어 가장 최근 항목으로
            self._items.pop(key, None)
            if len(self._items) >= self.max_entries:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (now, value)

    def update(self, key: K, change: Callable[[V], V]) -> None:
        """있으면 값만 바꿈 (넣은 시각은 그대로 - 만료가 늦춰지지 않음)"""
        with self._lock:
            hit = self._items.get(key)
            if hit:
                self._items[key] = (hit[0], change(hit[1]))

    def pop(self, key: K) -> None:
        with self._lock:
            self._items.pop(key, None)
