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
OTHER = Owner(user_id="01087654321")


@pytest.mark.asyncio
async def test_same_user_multiple_reservations(services, fake_launcher, valid_request):
    first = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="telegram", chat_id=123
    )
    second = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="web"
    )

    assert first.id != second.id
    assert {s["reservation_id"] for s in fake_launcher.launched} == {
        first.id,
        second.id,
    }
    assert len(services.reservations.list(USER, active=True)) == 2


@pytest.mark.asyncio
async def test_cancel_menu_lists_only_own_reservations(services, valid_request):
    mine = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="telegram", chat_id=123
    )
    await services.reservations.start(
        OTHER, valid_request, "010", "pw", origin="telegram", chat_id=456
    )

    listed = services.reservations.list(USER, chat_id=123, active=True)

    assert [r.id for r in listed] == [mine.id]


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


@pytest.mark.asyncio
async def test_callback_token_is_per_reservation(
    services, fake_launcher, valid_request
):
    """다른 예약의 토큰으로는 상태를 바꿀 수 없음"""
    from core.errors import NotAllowed

    first = await services.reservations.start(
        USER, valid_request, "010", "pw", origin="web"
    )
    await services.reservations.start(USER, valid_request, "010", "pw", origin="web")
    other_token = fake_launcher.launched[1]["callback_token"]

    with pytest.raises(NotAllowed):
        await services.reservations.handle_worker_event(
            WorkerEvent(reservation_id=first.id, token=other_token, status="success")
        )
