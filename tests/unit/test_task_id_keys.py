#!/usr/bin/env python3
"""
Simple test to verify task_id based Redis keys are working
"""
import time
from celery.result import AsyncResult
import redis
import sys

sys.path.insert(0, 'src')

from telegramBot.tasks import reservation_task
from config import web_settings

def test_task_id_keys():
    print("=" * 60)
    print("Testing task_id based Redis keys")
    print("=" * 60)

    # Connect to Redis
    redis_client = redis.Redis.from_url(web_settings.redis_url, db=web_settings.redis_db)

    # Clear any existing test keys
    for key in redis_client.keys("reservation_task:test_*"):
        redis_client.delete(key)
    print("✓ Cleared existing test keys\n")

    # Submit a test task with custom task ID
    task_id = "test_task_id_123"
    print(f"Submitting task with ID: {task_id}")

    task = reservation_task.apply_async(
        task_id=task_id,
        args=[
            1234567,  # chat_id
            {
                "korailId": web_settings.admin_korail_id,
                "korailPw": web_settings.admin_korail_pw,
                "date": "20251019",
                "dep_stn": "서울",
                "arr_stn": "부산",
                "dep_time": "000000",
                "arr_time": "235959",
                "train_type": "직통",
                "seat_type": "특실,일반실",
                "num_seats": 1,
            },
            "http://localhost:8390/reservation-complete",  # callback_url
        ],
    )

    print(f"Task ID: {task.id}")
    print(f"Task State: {task.state}\n")

    # Wait a bit for task to start
    print("Waiting 2 seconds for task to start...")
    time.sleep(2)

    # Check Redis for the task_id based key
    expected_key = f"reservation_task:{task_id}"
    print(f"\nChecking for Redis key: {expected_key}")

    if redis_client.exists(expected_key):
        print("✓ SUCCESS - Found task_id based Redis key!")
        value = redis_client.get(expected_key)
        print(f"  Key value: {value.decode() if value else 'None'}")
    else:
        print("✗ FAILED - task_id based Redis key not found")
        print("\nAll current Redis keys:")
        for key in redis_client.keys("reservation*"):
            print(f"  - {key.decode()}")

    # Revoke the task to stop it
    print("\nRevoking task...")
    task.revoke(terminate=True)

    # Clean up
    redis_client.delete(expected_key)
    print("✓ Cleanup complete\n")

if __name__ == "__main__":
    test_task_id_keys()
