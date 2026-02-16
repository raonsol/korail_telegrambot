#!/usr/bin/env python3
"""
Parallel Reservation Test for Celery Mode

Tests multiple concurrent reservation processes to verify:
1. Task isolation between different users
2. Redis state management consistency
3. Parallel task execution without conflicts
4. Callback handling for multiple simultaneous reservations
"""

import asyncio
import time
import redis
from datetime import datetime, timedelta
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from telegramBot.tasks import app as celery_app, reservation_task
from config import web_settings, celery_settings


class ParallelReservationTester:
    def __init__(self):
        self.redis_client = redis.Redis.from_url(
            web_settings.redis_url, db=web_settings.redis_db, decode_responses=True
        )
        print(f"✓ Connected to Redis: {web_settings.redis_url}")

    def cleanup_redis(self):
        """Clean up any existing test data"""
        pattern = "reservation:test_user_*"
        keys = self.redis_client.keys(pattern)
        if keys:
            self.redis_client.delete(*keys)
            print(f"✓ Cleaned up {len(keys)} test keys")

    def create_test_reservation_data(self, user_id: int):
        """Create test reservation data for a user"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

        return {
            "korail_id": os.getenv("ADMIN_KORAIL_ID", "test_user"),
            "korail_pw": os.getenv("ADMIN_KORAIL_PW", "test_pass"),
            "dep_date": tomorrow,
            "dep_station": "서울",
            "arr_station": "부산",
            "dep_time": "0600",
            "arr_time": "2400",
            "train_type": "KTX",
            "prefer_seat_type": "general",
        }

    def test_parallel_tasks(self, num_users=5):
        """
        Test parallel reservation tasks

        Args:
            num_users: Number of concurrent users to simulate
        """
        print(f"\n{'='*60}")
        print(f"Starting Parallel Reservation Test with {num_users} users")
        print(f"{'='*60}\n")

        # Clean up before test
        self.cleanup_redis()

        # Create test tasks
        tasks = []
        callback_url = f"http://localhost:{8390 if web_settings.is_dev else 8391}/reservation-complete"

        print(f"Submitting {num_users} reservation tasks...\n")

        for i in range(num_users):
            chat_id = 1000000 + i  # test_user_1000000, test_user_1000001, etc.
            reservation_data = self.create_test_reservation_data(chat_id)

            # Submit task to Celery
            task = reservation_task.apply_async(
                args=[chat_id, reservation_data, callback_url],
                task_id=f"test_reservation_{chat_id}",
            )
            tasks.append({"task": task, "chat_id": chat_id, "start_time": time.time()})

            print(f"  User {i+1} (chat_id={chat_id}): Task ID = {task.id}")

        print(f"\n✓ All {num_users} tasks submitted to Celery\n")

        # Monitor tasks
        print(f"{'='*60}")
        print("Monitoring Task Progress")
        print(f"{'='*60}\n")

        completed = 0
        failed = 0
        timeout = 60  # seconds to wait for tasks
        start_time = time.time()

        while completed + failed < num_users:
            if time.time() - start_time > timeout:
                print(f"\n⚠ Timeout reached ({timeout}s)")
                break

            for task_info in tasks:
                task = task_info["task"]
                chat_id = task_info["chat_id"]

                if task.state == "PENDING":
                    status = "⏳ Pending"
                elif task.state == "STARTED":
                    status = "🔄 Running"
                elif task.state == "SUCCESS":
                    if task_info.get("reported") != "SUCCESS":
                        elapsed = time.time() - task_info["start_time"]
                        print(f"  ✓ User {chat_id}: SUCCESS ({elapsed:.1f}s)")
                        task_info["reported"] = "SUCCESS"
                        completed += 1
                elif task.state == "FAILURE":
                    if task_info.get("reported") != "FAILURE":
                        print(f"  ✗ User {chat_id}: FAILED - {task.info}")
                        task_info["reported"] = "FAILURE"
                        failed += 1
                else:
                    status = f"? {task.state}"

            time.sleep(0.5)

        # Final results
        print(f"\n{'='*60}")
        print("Test Results")
        print(f"{'='*60}\n")

        for task_info in tasks:
            task = task_info["task"]
            chat_id = task_info["chat_id"]
            elapsed = time.time() - task_info["start_time"]

            # Check Redis state
            redis_status = self.redis_client.hget(f"reservation:{chat_id}", "status")

            print(f"User {chat_id}:")
            print(f"  Task State: {task.state}")
            print(f"  Redis Status: {redis_status or 'N/A'}")
            print(f"  Duration: {elapsed:.1f}s")

            if task.state == "SUCCESS":
                result = task.result
                print(f"  Result: {result.get('status', 'unknown')}")
            elif task.state == "FAILURE":
                print(f"  Error: {task.info}")
            print()

        # Verify isolation
        print(f"{'='*60}")
        print("Isolation Verification")
        print(f"{'='*60}\n")

        redis_keys = self.redis_client.keys("reservation:*")
        print(f"Total Redis keys: {len(redis_keys)}")
        print(f"Expected keys: {num_users}")

        if len(redis_keys) == num_users:
            print("✓ State isolation verified - each user has separate state")
        else:
            print(
                f"⚠ State isolation issue - found {len(redis_keys)} keys, expected {num_users}"
            )

        # Summary
        print(f"\n{'='*60}")
        print("Test Summary")
        print(f"{'='*60}\n")
        print(f"Total Users: {num_users}")
        print(f"Completed: {completed}")
        print(f"Failed: {failed}")
        print(f"Pending: {num_users - completed - failed}")

        if completed == num_users:
            print("\n✅ ALL TESTS PASSED - Parallel execution works correctly!")
        elif failed == 0:
            print(f"\n⚠ PARTIAL SUCCESS - {completed}/{num_users} completed")
        else:
            print(f"\n❌ SOME FAILURES - {failed}/{num_users} failed")

        return completed, failed

    def test_concurrent_state_access(self):
        """Test concurrent access to same user's state"""
        print(f"\n{'='*60}")
        print("Testing Concurrent State Access (Same User)")
        print(f"{'='*60}\n")

        chat_id = 9999999
        self.redis_client.delete(f"reservation:{chat_id}")

        # Submit multiple tasks for the same user
        callback_url = f"http://localhost:{8390 if web_settings.is_dev else 8391}/reservation-complete"
        reservation_data = self.create_test_reservation_data(chat_id)

        print(f"Submitting 3 concurrent tasks for same user (chat_id={chat_id})...\n")

        tasks = []
        for i in range(3):
            task = reservation_task.apply_async(
                args=[chat_id, reservation_data, callback_url],
                task_id=f"concurrent_test_{chat_id}_{i}",
            )
            tasks.append(task)
            print(f"  Task {i+1}: {task.id}")

        print("\nWaiting for completion (max 30s)...\n")

        # Wait for completion
        timeout = 30
        start_time = time.time()

        while time.time() - start_time < timeout:
            all_done = all(task.ready() for task in tasks)
            if all_done:
                break
            time.sleep(0.5)

        # Check results
        print("Results:")
        success_count = 0
        duplicate_count = 0

        for i, task in enumerate(tasks):
            print(f"\n  Task {i+1} ({task.id}):")
            print(f"    State: {task.state}")

            if task.successful():
                result = task.result
                print(f"    Status: {result.get('status')}")
                print(f"    Message: {result.get('message', '')[:50]}")

                if result.get("status") == "already_completed":
                    duplicate_count += 1
                elif result.get("status") == "success":
                    success_count += 1

        print(f"\n{'='*60}")
        print("Concurrent Access Test Summary")
        print(f"{'='*60}\n")
        print(f"Successful reservations: {success_count}")
        print(f"Duplicate detections: {duplicate_count}")

        if success_count == 1 and duplicate_count >= 1:
            print(
                "\n✅ RACE CONDITION HANDLING WORKS - Only one task succeeded, others detected completion"
            )
        elif success_count > 1:
            print(
                f"\n❌ RACE CONDITION DETECTED - Multiple tasks succeeded ({success_count})"
            )
        else:
            print("\n⚠ UNEXPECTED RESULT")

        return success_count, duplicate_count


def main():
    print(
        """
╔════════════════════════════════════════════════════════════╗
║  Korail Telegram Bot - Parallel Reservation Test Suite    ║
╚════════════════════════════════════════════════════════════╝
    """
    )

    # Check if Celery worker is running
    print("Checking Celery worker status...")
    try:
        inspect = celery_app.control.inspect()
        active = inspect.active()

        if active:
            print(f"✓ Celery workers detected: {list(active.keys())}")
        else:
            print("⚠ WARNING: No active Celery workers found!")
            print("  Please start worker with: make dev-celery or make run-celery")
            print("  Continuing anyway for testing purposes...")
    except Exception as e:
        print(f"⚠ Could not check Celery status: {e}")
        print("  Continuing anyway for testing purposes...")

    tester = ParallelReservationTester()

    # Test 1: Parallel tasks with different users
    print("\n" + "=" * 60)
    print("TEST 1: Parallel Tasks (Different Users)")
    print("=" * 60)
    completed, failed = tester.test_parallel_tasks(num_users=5)

    # Test 2: Concurrent access to same user state
    print("\n" + "=" * 60)
    print("TEST 2: Concurrent State Access (Same User)")
    print("=" * 60)
    success_count, duplicate_count = tester.test_concurrent_state_access()

    # Final summary
    print(f"\n{'='*60}")
    print("FINAL TEST SUMMARY")
    print(f"{'='*60}\n")
    print(f"Test 1 - Parallel Tasks: {completed}/5 completed, {failed}/5 failed")
    print(
        f"Test 2 - Race Condition: {success_count} succeeded, {duplicate_count} detected duplicates"
    )

    # Cleanup
    print("\nCleaning up test data...")
    tester.cleanup_redis()
    print("✓ Cleanup complete")


if __name__ == "__main__":
    main()
