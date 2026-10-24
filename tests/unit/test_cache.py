"""core.cache.TTLCache"""

from core.cache import TTLCache


def test_expires_and_evicts_oldest():
    now = [0.0]
    cache = TTLCache(ttl=10, max_entries=2, clock=lambda: now[0])
    cache.put("a", 1)
    cache.put("b", 2)
    # 이미 있는 키를 다시 넣으면 다른 항목을 밀어내지 않음
    cache.put("b", 3)
    assert (cache.get("a"), cache.get("b")) == (1, 3)
    cache.put("c", 4)  # 가득 차면 가장 오래 넣은 것부터
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (None, 3, 4)

    now[0] = 11
    assert cache.get("b") is None
    cache.put("d", 5)  # 만료된 항목은 넣을 때 정리
    assert list(cache._items) == ["d"]


def test_update_keeps_timestamp_and_pop():
    now = [0.0]
    cache = TTLCache(ttl=10, max_entries=5, clock=lambda: now[0])
    cache.put("a", [1, 2])
    now[0] = 5
    cache.update("a", lambda v: v[1:])
    cache.update("missing", lambda v: v)
    assert cache.get("a") == [2] and cache.get("missing") is None
    now[0] = 10
    assert cache.get("a") is None  # 바꿔도 만료는 늦춰지지 않음
    cache.put("b", 1)
    cache.pop("b")
    assert cache.get("b") is None
