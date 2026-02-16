#!/usr/bin/env python3
"""
Integration test to verify task_id based Redis keys with a real Redis instance.
"""
from unittest.mock import Mock, patch

import redis
import sys
import pytest

sys.path.insert(0, "src")

from telegramBot.tasks import reservation_task
from config import web_settings


@pytest.mark.requires_redis
@pytest.mark.integration
def test_task_id_keys():
    redis_client = redis.Redis.from_url(
        web_settings.redis_url,
        db=web_settings.redis_db,
        decode_responses=True,
    )
    try:
        redis_client.ping()
    except Exception as exc:
        pytest.skip(f"Redis is not reachable in this environment: {exc}")

    task_id = "test_task_id_123"
    expected_key = f"reservation_task:{task_id}"

    # Ensure clean state
    redis_client.delete(expected_key)

    # Run task locally (no worker needed) but with a real Redis backend.
    # Login is forced to fail quickly so this test doesn't depend on Korail.
    mock_response = Mock()
    mock_response.raise_for_status = Mock()

    with patch("telegramBot.tasks.requests.post", return_value=mock_response), patch(
        "telegramBot.tasks.ReserveHandler"
    ) as mock_handler_class:
        mock_handler = Mock()
        mock_handler.login = Mock(return_value=False)
        mock_handler_class.return_value = mock_handler

        reservation_task.apply(
            args=[
                1234567,
                {
                    "korail_id": "test_user",
                    "korail_pw": "test_pass",
                    "dep_date": "20251019",
                    "dep_station": "서울",
                    "arr_station": "부산",
                    "dep_time": "090000",
                    "arr_time": "1200",
                    "train_type": "KTX",
                    "prefer_seat_type": "general",
                },
                "http://localhost:8390/reservation_callback",
            ],
            task_id=task_id,
        )

    assert redis_client.exists(expected_key), f"Missing Redis key: {expected_key}"
    assert redis_client.hget(expected_key, "status") == "running"

    # Cleanup
    redis_client.delete(expected_key)


if __name__ == "__main__":
    test_task_id_keys()
