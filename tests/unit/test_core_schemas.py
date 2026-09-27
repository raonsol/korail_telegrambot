"""ReservationRequest 검증 규칙 (봇/웹 공통)"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from core.schemas import (
    ReservationRequest,
    format_phone,
    is_valid_phone,
    normalize_phone,
    now_kst,
)


def _request(**overrides):
    data = {
        "dep_date": now_kst().date() + timedelta(days=3),
        "src_station": "서울",
        "dst_station": "부산",
        "dep_time": "0900",
        "max_dep_time": "1200",
    }
    data.update(overrides)
    return ReservationRequest(**data)


class TestReservationRequest:
    def test_valid_defaults(self):
        req = _request()
        assert req.train_type == "KTX"
        assert req.seat_type == "general"

    def test_accepts_compact_date(self):
        future = now_kst().date() + timedelta(days=3)
        req = _request(dep_date=future.strftime("%Y%m%d"))
        assert req.dep_date == future
        assert req.dep_date_compact == future.strftime("%Y%m%d")

    def test_accepts_iso_date(self):
        future = now_kst().date() + timedelta(days=3)
        assert _request(dep_date=future.isoformat()).dep_date == future

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"dep_date": now_kst().date() - timedelta(days=1)}, "이전"),
            ({"dep_date": now_kst().date() + timedelta(days=400)}, "먼 미래"),
            ({"dst_station": "서울"}, "같습니다"),
            ({"dep_time": "2500"}, "올바르지 않은"),
            ({"dep_time": "9am"}, "HHMM"),
            ({"max_dep_time": "0800"}, "최대 출발 시각"),
            ({"src_station": "   "}, "역 이름"),
            ({"train_type": "SRT"}, ""),
            ({"seat_type": "vip"}, ""),
        ],
    )
    def test_rejects_invalid(self, overrides, message):
        with pytest.raises(ValidationError) as exc:
            _request(**overrides)
        assert message in str(exc.value)

    def test_rejects_past_time_today(self):
        now = now_kst()
        if now.hour == 0 and now.minute == 0:
            pytest.skip("no earlier time today")
        with pytest.raises(ValidationError, match="현재 시각"):
            _request(dep_date=now.date(), dep_time="0000", max_dep_time="2359")

    def test_strips_station_names(self):
        req = _request(src_station=" 서울 ", dst_station="부산 ")
        assert (req.src_station, req.dst_station) == ("서울", "부산")


class TestPhoneHelpers:
    def test_normalize_and_format(self):
        assert normalize_phone("010-1234-5678") == "01012345678"
        assert format_phone("01012345678") == "010-1234-5678"
        assert format_phone("123") == "123"

    @pytest.mark.parametrize(
        "phone, valid",
        [
            ("010-1234-5678", True),
            ("01012345678", True),
            ("0101234567", False),
            ("02012345678", False),
            ("", False),
        ],
    )
    def test_is_valid_phone(self, phone, valid):
        assert is_valid_phone(phone) is valid
