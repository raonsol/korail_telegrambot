#!/usr/bin/env python3
"""
Test Korail duplicate reservation behavior
"""

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, "src")

from pykorail import TrainType, ReserveOption
from telegramBot.korail_client import create_korail_client

korail_id = os.getenv("ADMIN_KORAIL_ID")
korail_pw = os.getenv("ADMIN_KORAIL_PW")

if not korail_id or not korail_pw:
    print("❌ ADMIN_KORAIL_ID or ADMIN_KORAIL_PW not set")
    sys.exit(1)

tomorrow = (datetime.now() + timedelta(days=1)).replace(
    hour=6, minute=0, second=0, microsecond=0
)

print("Testing consecutive reservations with same account...")
print("=" * 60)

# Create 5 separate Korail instances (simulating 5 workers)
for i in range(5):
    print(f"\nAttempt {i+1}:")
    try:
        korail = create_korail_client()
        try:
            korail.login(korail_id, korail_pw)
        except Exception as e:
            print(f"  ❌ Login failed: {e}")
            continue

        print(f"  ✓ Login successful")

        # Search for trains
        trains = korail.trains.search(
            "서울", "부산", depart_after=tomorrow, train_type=TrainType.KTX
        )

        if not trains:
            print(f"  ⚠ No trains found")
            continue

        train = trains[0]
        print(f"  ✓ Found train: {train}")

        # Try to reserve
        try:
            reservation = korail.reservations.create(
                train, option=ReserveOption.GENERAL_FIRST
            )
            print(f"  ✅ Reservation SUCCESS: {reservation}")
        except Exception as e:
            error_msg = str(e)
            print(f"  ❌ Reservation FAILED: {error_msg}")

            # Check if it's duplicate error
            if "동일한 예약" in error_msg or "WRR800029" in error_msg:
                print(f"  → This is a DUPLICATE RESERVATION error from Korail API")

    except Exception as e:
        print(f"  ❌ Exception: {e}")

print("\n" + "=" * 60)
print("Checking final reservation list...")

try:
    korail = create_korail_client()
    korail.login(korail_id, korail_pw)
    reservations = korail.reservations.all()
    print(f"Total reservations: {len(reservations)}")
    for i, res in enumerate(reservations, 1):
        print(f"  {i}. {res}")
except Exception as e:
    print(f"❌ Error checking reservations: {e}")
