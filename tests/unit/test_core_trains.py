"""새 예약 화면의 열차 목록 조회 (출구 순서·차단, 이어서 조회, 캐시)"""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from pykorail import AccessRestrictedError, NoResultsError

from core.egress import EgressPool, MemoryGate, parse_egresses
from core.errors import KorailUnavailable, ServiceError
from core.schemas import now_kst
from core.trains import MAX_PAGES, TrainQuery, TrainSearchService


class FakeTrain:
    def __init__(
        self, dep, arr="1200", no="101", general=True, special=False, wait=False
    ):
        self.train_no = no
        self.train_type_name = "KTX"
        self.dep_time = f"{dep}00"
        self.arr_time = f"{arr}00"
        self.duration_minutes = 150
        self._general, self._special, self._wait = general, special, wait

    def has_general_seat(self):
        return self._general

    def has_special_seat(self):
        return self._special

    def has_seat(self):
        return self._general or self._special

    def has_waiting_list(self):
        return self._wait


def _query(start="0900", end="1200", days=7, train_type="KTX"):
    return TrainQuery(
        dep_date=(now_kst() + timedelta(days=days)).date(),
        src_station="서울",
        dst_station="부산",
        dep_time=start,
        max_dep_time=end,
        train_type=train_type,
    )


def _service(pages, gate=None, device=None):
    """pages: search 호출마다 돌려줄 열차 목록 (예외면 발생)"""
    client = Mock()
    client.trains.search = Mock(side_effect=pages)
    factory = Mock(return_value=client)
    pool = EgressPool(parse_egresses(""))
    if gate is not None:
        pool._gates[pool.ids[0]] = gate
    service = TrainSearchService(
        pool,
        device_for=lambda _: device,
        client_factory=factory,
        sleep=Mock(),
    )
    return service, client, factory


class TestTrainSearch:
    def test_lists_trains_in_range_with_seat_status(self):
        device = {"profile_id": "p", "android_id": "a" * 16}
        service, client, factory = _service(
            [
                [
                    FakeTrain("0900", no="101", general=False, special=True),
                    FakeTrain("1000", no="103", general=False, wait=True),
                    FakeTrain("1200", no="105"),  # 끝 시각 이후 (예약 조건처럼 제외)
                ]
            ],
            device=device,
        )

        result = service.search("01012345678", _query())

        assert [t.train_no for t in result.trains] == ["101", "103"]
        first, second = result.trains
        assert (first.general_seat, first.special_seat, first.waitlist) == (
            False,
            True,
            False,
        )
        assert second.waitlist is True
        assert first.dep_time == "0900" and first.arr_time == "1200"
        assert result.truncated is False
        # 비밀번호 없이 계정의 기기 신원으로 조회하고, 역 마스터 확인 요청은 생략
        assert factory.call_args.kwargs["device"] == device
        assert client.trains.validate_stations is False
        kwargs = client.trains.search.call_args.kwargs
        assert kwargs["include_no_seats"] is True
        assert kwargs["train_type"] == "100"  # TrainType.KTX
        client.close.assert_called_once()

    def test_continues_after_last_train_until_end(self):
        service, client, _ = _service(
            [
                [FakeTrain("0900", no="1"), FakeTrain("0930", no="2")],
                [FakeTrain("1000", no="3"), FakeTrain("1300", no="4")],
            ]
        )
        result = service.search("01012345678", _query())
        assert [t.train_no for t in result.trains] == ["1", "2", "3"]
        assert client.trains.search.call_count == 2
        # 두 번째 조회는 앞 결과의 마지막 열차 1분 뒤부터
        second = client.trains.search.call_args_list[1].kwargs["depart_after"]
        assert second.strftime("%H%M") == "0931"

    def test_stops_after_max_pages(self):
        pages = [[FakeTrain(f"{9 + i:02d}00", no=str(i))] for i in range(MAX_PAGES + 2)]
        service, client, _ = _service(pages)
        result = service.search("01012345678", _query(end="2359"))
        assert client.trains.search.call_count == MAX_PAGES
        assert result.truncated is True

    def test_next_page_empty(self):
        """앞 결과가 모두 시간대 안이면 이어서 조회하고, 더 없으면 그대로 끝"""
        service, client, _ = _service([[FakeTrain("0900")], NoResultsError()])
        assert [t.dep_time for t in service.search("01012345678", _query()).trains] == [
            "0900"
        ]
        assert client.trains.search.call_count == 2

    def test_no_results(self):
        service, _, _ = _service([NoResultsError()])
        assert service.search("01012345678", _query()).trains == []

    def test_caches_same_query(self):
        page = [FakeTrain("0900"), FakeTrain("1300")]
        service, client, _ = _service([page, page])
        service.search("01012345678", _query())
        service.search("01087654321", _query())
        assert client.trains.search.call_count == 1
        service.search("01012345678", _query(start="1000"))
        assert client.trains.search.call_count == 2

    def test_past_range_today_skips_request(self):
        now = now_kst()
        if now.strftime("%H%M") < "0001":
            pytest.skip("자정 직후에는 지난 시간대를 만들 수 없음")
        service, client, factory = _service([])
        query = TrainQuery(now.date(), "서울", "부산", "0000", "0001", "KTX")
        assert service.search("01012345678", query).trains == []
        factory.assert_not_called()

    def test_block_pauses_egress(self):
        gate = MemoryGate()
        service, _, _ = _service(
            [AccessRestrictedError(code="-2000", msg="blocked", status_code=403)],
            gate=gate,
        )
        with pytest.raises(KorailUnavailable):
            service.search("01012345678", _query())
        assert gate.blocked_for() > 0

    def test_blocked_egress_does_not_request(self):
        gate = MemoryGate()
        gate.report_block()
        service, client, _ = _service([[FakeTrain("0900")]], gate=gate)
        with pytest.raises(KorailUnavailable):
            service.search("01012345678", _query())
        client.trains.search.assert_not_called()

    def test_busy_egress(self):
        gate = Mock(
            blocked_for=Mock(return_value=0), reserve_slot=Mock(return_value=60)
        )
        service, client, _ = _service([[FakeTrain("0900")]], gate=gate)
        with pytest.raises(ServiceError) as e:
            service.search("01012345678", _query())
        assert e.value.code == "TRAIN_SEARCH_BUSY"
        client.trains.search.assert_not_called()

    def test_waits_for_egress_slot(self):
        gate = Mock(
            blocked_for=Mock(return_value=0), reserve_slot=Mock(return_value=1.5)
        )
        service, _, _ = _service([[FakeTrain("0900"), FakeTrain("1300")]], gate=gate)
        service.search("01012345678", _query())
        service._sleep.assert_called_once_with(1.5)

    def test_unexpected_error_is_reported_as_failure(self):
        service, client, _ = _service([RuntimeError("boom")])
        with pytest.raises(ServiceError) as e:
            service.search("01012345678", _query())
        assert e.value.code == "TRAIN_SEARCH_FAILED"
        client.close.assert_called_once()
