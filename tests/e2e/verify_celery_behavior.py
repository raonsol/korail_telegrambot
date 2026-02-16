#!/usr/bin/env python3
"""
Verify what actually happens in Celery worker reservation
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, "src")

from telegramBot.korail_client import ReserveHandler
from korail2 import TrainType, ReserveOption

korail_id = os.getenv("ADMIN_KORAIL_ID")
korail_pw = os.getenv("ADMIN_KORAIL_PW")

tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

print("Simulating Celery worker behavior...")
print("=" * 60)

# Simulate 5 separate worker processes
for i in range(5):
    print(f"\n{'='*60}")
    print(f"Worker {i} (simulating separate process)")
    print(f"{'='*60}")

    # Each worker creates its own ReserveHandler (as Celery does)
    handler = ReserveHandler()

    # Login
    if not handler.login(korail_id, korail_pw):
        print(f"❌ Login failed")
        continue

    print(f"✓ Login successful")

    # Call reserve_single_attempt (as tasks.py does)
    result = handler.reserve_single_attempt(
        depDate=tomorrow,
        srcLocate="서울",
        dstLocate="부산",
        depTime="080000",
        trainType=TrainType.KTX,
        special=ReserveOption.GENERAL_FIRST,
        maxDepTime="2400",
    )

    print(f"Result: {result['success']}")
    print(f"Message: {result.get('result', result.get('error'))}")

    if result["success"]:
        if result["result"] == "duplicate_reservation":
            print(f"  → DUPLICATE DETECTED by Korail API")
        else:
            print(f"  → ACTUAL RESERVATION: {str(result['result'])[:100]}")

print(f"\n{'='*60}")
print("Checking final reservation count...")
print(f"{'='*60}")

try:
    from korail2 import Korail

    korail = Korail(korail_id, korail_pw, auto_login=False)
    korail.login()
    reservations = korail.reservations()
    print(f"Total reservations: {len(reservations)}")
    for idx, res in enumerate(reservations, 1):
        print(f"  {idx}. {res}")
except Exception as e:
    print(f"Error: {e}")
