#!/usr/bin/env python3
"""
Test rapid consecutive reservations (simulating parallel behavior)
"""
import sys
import os
from datetime import datetime, timedelta
import threading
import time

sys.path.insert(0, "src")

from korail2 import Korail, TrainType, ReserveOption

korail_id = os.getenv("ADMIN_KORAIL_ID")
korail_pw = os.getenv("ADMIN_KORAIL_PW")

tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

results = []
lock = threading.Lock()


def try_reserve(worker_id):
    try:
        korail = Korail(korail_id, korail_pw, auto_login=False)
        korail.login()

        trains = korail.search_train(
            "서울", "부산", tomorrow, "070000", train_type=TrainType.KTX
        )
        if not trains:
            with lock:
                results.append((worker_id, "NO_TRAINS", None))
            return

        train = trains[0]

        try:
            reservation = korail.reserve(train, option=ReserveOption.GENERAL_FIRST)
            with lock:
                results.append((worker_id, "SUCCESS", str(reservation)))
        except Exception as e:
            with lock:
                results.append((worker_id, "FAILED", str(e)))

    except Exception as e:
        with lock:
            results.append((worker_id, "ERROR", str(e)))


print("Testing 5 SIMULTANEOUS reservations...")
print("=" * 60)

threads = []
for i in range(5):
    t = threading.Thread(target=try_reserve, args=(i,))
    threads.append(t)

# Start all threads at nearly the same time
start_time = time.time()
for t in threads:
    t.start()

# Wait for all to complete
for t in threads:
    t.join()

elapsed = time.time() - start_time

print(f"\nAll threads completed in {elapsed:.2f}s\n")

# Sort results by worker_id
results.sort(key=lambda x: x[0])

success_count = 0
for worker_id, status, msg in results:
    print(f"Worker {worker_id}: {status}")
    if msg:
        print(f"  → {msg[:100]}")
    if status == "SUCCESS":
        success_count += 1

print(f"\n{'='*60}")
print(f"SUCCESS: {success_count}/5")
print(f"{'='*60}")

# Check actual reservations
print("\nChecking actual reservations...")
try:
    korail = Korail(korail_id, korail_pw, auto_login=False)
    korail.login()
    reservations = korail.reservations()
    print(f"Total reservations: {len(reservations)}")
except Exception as e:
    print(f"Error: {e}")
