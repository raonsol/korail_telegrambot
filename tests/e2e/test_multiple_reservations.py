"""
Test multiple reservation support

Each reservation is identified by its own ``reservation_id`` (issued by
ReservationService). The same user can run several reservations at once,
from Telegram and the web, and each one is tracked/cancelled independently.

See CLAUDE.md "Multiple Reservation Support" section for details.
"""

import pytest

from core.schemas import Owner, WorkerEvent

USER = Owner(user_id="01012345678")


@pytest.mark.asyncio
async def test_completion_only_affects_its_reservation(
    services, fake_launcher, valid_request
):
    first = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="telegram", chat_id=123
    )
    second = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="telegram", chat_id=123
    )
    token = fake_launcher.launched[1]["callback_token"]

    await services.reservations.handle_worker_event(
        WorkerEvent(reservation_id=second.id, token=token, status="success")
    )

    assert services.reservations.get(first.id, USER).is_active
    assert services.reservations.get(second.id, USER).status.value == "success"
