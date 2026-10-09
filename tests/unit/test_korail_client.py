"""
Unit tests for Korail API client (korail_client.py)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from pykorail import (
    TrainType,
    ReserveOption,
    SoldOutError,
    NoResultsError,
    LoginFailedError,
    StationNotFoundError,
    PastDepartureError,
    HttpStatusError,
    AccessRestrictedError,
)


class TestReserveHandler:
    """Test ReserveHandler class"""

    @pytest.fixture
    def reserve_handler(self):
        """Create a ReserveHandler instance for testing"""
        from telegramBot.korail_client import ReserveHandler

        return ReserveHandler()

    def test_init(self, reserve_handler):
        """Test ReserveHandler initialization"""
        assert reserve_handler.korail_client is None
        assert reserve_handler.loginSuc is False
        assert reserve_handler.reserveInfo["reserveSuc"] is False
        assert reserve_handler.interval == 1

    def test_login_success(self, reserve_handler, mock_korail_client):
        """Test successful login"""
        with patch("telegramBot.korail_client.Korail", return_value=mock_korail_client):
            result = reserve_handler.login("test_user", "test_password")

            assert result is True
            assert reserve_handler.loginSuc is True
            assert reserve_handler.korail_client is not None
            assert reserve_handler.username == "test_user"
            assert reserve_handler.password == "test_password"
            mock_korail_client.login.assert_called_once_with(
                "test_user", "test_password"
            )

    def test_login_failure(self, reserve_handler):
        """Test failed login (pykorail raises LoginFailedError)"""
        mock_client = Mock()
        mock_client.login = Mock(side_effect=LoginFailedError("비밀번호 오류"))

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            result = reserve_handler.login("test_user", "wrong_password")

            assert result is False
            assert reserve_handler.loginSuc is False
            assert reserve_handler.korail_client is None
            mock_client.close.assert_called_once()

    def test_login_failure_reason_uses_korail_message(self, reserve_handler):
        """A pykorail/Korail message is kept as the failure reason, without '(None)'"""
        msg = "휴대폰 번호로 로그인하려면 하이픈을 넣어야 합니다: '01012345678' 대신 '010-1234-5678'"
        mock_client = Mock()
        mock_client.login = Mock(side_effect=LoginFailedError(msg))

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            assert reserve_handler.login("01012345678", "pw") is False

        assert reserve_handler.loginError == msg
        assert "(None)" not in reserve_handler.loginError

    def test_login_failure_reason_without_server_reason(self, reserve_handler):
        """pykorail's fallback text (no server reason/code) is not shown as a password error"""
        mock_client = Mock()
        mock_client.login = Mock(
            side_effect=LoginFailedError(
                "아이디 또는 비밀번호가 올바르지 않습니다", None
            )
        )

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            assert reserve_handler.login("me@example.com", "pw") is False

        assert "사유 없이 로그인을 거부" in reserve_handler.loginError

    def test_login_failure_reason_with_server_code(self, reserve_handler):
        """A real server rejection (with code) keeps the server message"""
        mock_client = Mock()
        mock_client.login = Mock(
            side_effect=LoginFailedError(
                "로그인 정보를 다시 확인해 주세요.", "WRR000101"
            )
        )

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            assert reserve_handler.login("me@example.com", "pw") is False

        assert reserve_handler.loginError == "로그인 정보를 다시 확인해 주세요."

    def test_login_failure_reason_for_http_rejection(self, reserve_handler):
        """A non-block HTTP rejection (e.g. 502) is not shown as a password/network error"""
        mock_client = Mock()
        mock_client.login = Mock(side_effect=HttpStatusError(502, "Bad Gateway"))

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            assert reserve_handler.login("test_user", "test_password") is False

        assert (
            reserve_handler.loginError
            == "코레일 서버가 요청을 거절했습니다: Bad Gateway"
        )
        assert reserve_handler.loginBlocked is False
        mock_client.close.assert_called_once()

    def test_login_failure_reason_for_network_error(self, reserve_handler):
        """Non-Korail errors get a generic reason instead of raw exception text"""
        mock_client = Mock()
        mock_client.login = Mock(side_effect=ConnectionError("proxy refused"))

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            assert reserve_handler.login("test_user", "test_password") is False

        assert "코레일 서버에 연결하지 못했습니다" in reserve_handler.loginError

    def test_login_success_clears_failure_reason(
        self, reserve_handler, mock_korail_client
    ):
        """A successful login clears the previous failure reason"""
        reserve_handler.loginError = "이전 실패"

        with patch("telegramBot.korail_client.Korail", return_value=mock_korail_client):
            assert reserve_handler.login("test_user", "test_password") is True

        assert reserve_handler.loginError == ""

    def test_relogin_failure_keeps_previous_client(self, reserve_handler):
        """A failed re-login keeps the existing session instead of dropping it"""
        previous_client = Mock()
        reserve_handler.korail_client = previous_client
        failing_client = Mock()
        failing_client.login = Mock(side_effect=LoginFailedError())

        with patch("telegramBot.korail_client.Korail", return_value=failing_client):
            assert reserve_handler.login("test_user", "test_password") is False

        assert reserve_handler.korail_client is previous_client
        previous_client.close.assert_not_called()

    def test_relogin_success_replaces_previous_client(
        self, reserve_handler, mock_korail_client
    ):
        """A successful re-login closes and replaces the previous session"""
        previous_client = Mock()
        reserve_handler.korail_client = previous_client

        with patch("telegramBot.korail_client.Korail", return_value=mock_korail_client):
            assert reserve_handler.login("test_user", "test_password") is True

        previous_client.close.assert_called_once()
        assert reserve_handler.korail_client is mock_korail_client

    def test_close(self, reserve_handler, mock_korail_client):
        """Test close releases the Korail client"""
        reserve_handler.korail_client = mock_korail_client

        reserve_handler.close()

        mock_korail_client.close.assert_called_once()
        assert reserve_handler.korail_client is None

    def test_login_exception(self, reserve_handler):
        """Test login with exception"""
        mock_client = Mock()
        mock_client.login = Mock(side_effect=Exception("Network error"))

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            result = reserve_handler.login("test_user", "test_password")

            assert result is False
            assert reserve_handler.loginSuc is False

    def test_update_reserve_info(self, reserve_handler):
        """Test _update_reserve_info method"""
        reserve_handler._update_reserve_info(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert reserve_handler.reserveInfo["depDate"] == "20250115"
        assert reserve_handler.reserveInfo["srcLocate"] == "서울"
        assert reserve_handler.reserveInfo["dstLocate"] == "부산"
        assert reserve_handler.reserveInfo["depTime"] == "090000"
        assert reserve_handler.reserveInfo["trainType"] == TrainType.KTX
        assert reserve_handler.reserveInfo["special"] == ReserveOption.GENERAL_FIRST
        assert reserve_handler.reserveInfo["maxDepTime"] == "1200"

    def test_search_trains_success(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test successful train search"""
        mock_korail_client.trains.search = Mock(return_value=[sample_train_data])
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "1200",
        }

        trains = reserve_handler._search_trains()

        assert len(trains) == 1
        assert trains[0] == sample_train_data
        args, kwargs = mock_korail_client.trains.search.call_args
        assert args == ("서울", "부산")
        assert kwargs["train_type"] == TrainType.KTX
        assert kwargs["depart_after"].strftime("%Y%m%d%H%M%S") == "20250115090000"

    def test_search_trains_no_results(self, reserve_handler, mock_korail_client):
        """Test train search with no results"""
        mock_korail_client.trains.search = Mock(side_effect=NoResultsError())
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "1200",
        }

        trains = reserve_handler._search_trains()

        assert trains == []

    def test_search_trains_exceeds_max_time(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test train search filters out trains exceeding maxDepTime"""
        mock_korail_client.trains.search = Mock(return_value=[sample_train_data])
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "0800",  # Earlier than train departure
        }

        trains = reserve_handler._search_trains()

        assert trains == []

    def test_search_trains_filters_each_train_by_max_time(
        self, reserve_handler, mock_korail_client
    ):
        """Only trains departing before maxDepTime are kept"""
        early, late = Mock(dep_time="093000"), Mock(dep_time="120000")
        mock_korail_client.trains.search = Mock(return_value=[early, late])
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "1200",
        }

        assert reserve_handler._search_trains() == [early]

    @pytest.mark.parametrize(
        "depDate, depTime, expected",
        [
            # 오늘 이미 지난 시각 -> 현재 시각부터 검색
            ("20260315", "060000", "20260315103000"),
            # 오늘 아직 오지 않은 시각 -> 그대로
            ("20260315", "150000", "20260315150000"),
            # 미래 날짜 -> 그대로
            ("20260316", "060000", "20260316060000"),
            # 이미 지난 날짜 -> 그대로 (pykorail 이 PastDepartureError 로 거부)
            ("20260314", "150000", "20260314150000"),
        ],
    )
    def test_depart_after(self, reserve_handler, depDate, depTime, expected):
        """_depart_after clamps past times of today to now (KST)"""
        from freezegun import freeze_time

        reserve_handler.reserveInfo.update({"depDate": depDate, "depTime": depTime})
        # 2026-03-15 10:30 KST
        with freeze_time("2026-03-15 01:30:00"):
            result = reserve_handler._depart_after()

        assert result.strftime("%Y%m%d%H%M%S") == expected
        assert result.utcoffset().total_seconds() == 9 * 3600

    def test_try_reserve_success(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test successful reservation attempt"""
        mock_reservation = Mock()
        mock_korail_client.reservations.create = Mock(return_value=mock_reservation)
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {"special": ReserveOption.GENERAL_FIRST}

        result = reserve_handler._try_reserve(sample_train_data)

        assert result == mock_reservation
        mock_korail_client.reservations.create.assert_called_once_with(
            sample_train_data, option=ReserveOption.GENERAL_FIRST
        )

    def test_try_reserve_sold_out(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reservation attempt when sold out"""
        mock_korail_client.reservations.create = Mock(side_effect=SoldOutError())
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {"special": ReserveOption.GENERAL_FIRST}

        result = reserve_handler._try_reserve(sample_train_data)

        assert result is None

    def test_reserve_single_attempt_success(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reserve_single_attempt with successful reservation"""
        mock_reservation = Mock()
        mock_korail_client.trains.search = Mock(return_value=[sample_train_data])
        mock_korail_client.reservations.create = Mock(return_value=mock_reservation)
        reserve_handler.korail_client = mock_korail_client

        result = reserve_handler.reserve_single_attempt(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert result["success"] is True
        assert result["result"] == mock_reservation
        assert result["error"] is None
        assert reserve_handler.reserveInfo["reserveSuc"] is True

    def test_reserve_single_attempt_no_trains(
        self, reserve_handler, mock_korail_client
    ):
        """Test reserve_single_attempt with no available trains"""
        mock_korail_client.trains.search = Mock(return_value=[])
        reserve_handler.korail_client = mock_korail_client

        result = reserve_handler.reserve_single_attempt(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert result["success"] is False
        assert result["result"] is None
        assert result["error"] == "No trains available"

    def test_reserve_single_attempt_all_sold_out(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reserve_single_attempt when all trains are sold out"""
        mock_korail_client.trains.search = Mock(return_value=[sample_train_data])
        mock_korail_client.reservations.create = Mock(side_effect=SoldOutError())
        reserve_handler.korail_client = mock_korail_client

        result = reserve_handler.reserve_single_attempt(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert result["success"] is False
        assert result["result"] is None
        assert result["error"] == "All trains sold out"

    def test_reserve_single_attempt_duplicate_reservation(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reserve_single_attempt with duplicate reservation"""
        mock_korail_client.trains.search = Mock(return_value=[sample_train_data])
        mock_korail_client.reservations.create = Mock(
            side_effect=Exception("동일한 예약 내역이 있으니 확인하시기 바랍니다")
        )
        reserve_handler.korail_client = mock_korail_client

        result = reserve_handler.reserve_single_attempt(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert result["success"] is True
        assert result["result"] == "duplicate_reservation"
        assert result["error"] is None
        assert reserve_handler.reserveInfo["reserveSuc"] is True

    @pytest.mark.parametrize(
        "error",
        [
            StationNotFoundError(["광주열분"], ["광주", "광주송정"]),
            PastDepartureError(datetime(2025, 1, 15, 9), datetime(2025, 1, 16, 9)),
        ],
    )
    def test_reserve_single_attempt_fatal_error(
        self, reserve_handler, mock_korail_client, error
    ):
        """Errors that retrying cannot fix are flagged as fatal"""
        mock_korail_client.trains.search = Mock(side_effect=error)
        reserve_handler.korail_client = mock_korail_client

        result = reserve_handler.reserve_single_attempt(
            depDate="20250115",
            srcLocate="광주열분",
            dstLocate="부산",
            depTime="090000",
            trainType=TrainType.KTX,
            special=ReserveOption.GENERAL_FIRST,
            maxDepTime="1200",
        )

        assert result["success"] is False
        assert result["fatal"] is True
        assert result["error"] == str(error)


BLOCK_MESSAGE = "매크로 등 미허가 도구 사용 시 이용이 제한될 수 있습니다."


def _block(status=403):
    return AccessRestrictedError(status, BLOCK_MESSAGE, "-2000")


class TestKorailBlockLogging:
    """Korail server block responses (code -2000) are written to the server log"""

    def test_proxy_applies_only_to_korail_session(self):
        from telegramBot.korail_client import create_korail_client

        client = create_korail_client("socks5h://user:pw@100.64.0.2:1080")
        try:
            assert client._api._session.proxies == {
                "http": "socks5h://user:pw@100.64.0.2:1080",
                "https": "socks5h://user:pw@100.64.0.2:1080",
            }
        finally:
            client.close()

        direct = create_korail_client()
        try:
            assert not direct._api._session.proxies
        finally:
            direct.close()

    def test_handler_uses_its_egress(self):
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler("socks5h://h:1080", "home1")
        with patch("telegramBot.korail_client.create_korail_client") as create:
            handler.login("010-1234-5678", "pw")
        create.assert_called_once_with("socks5h://h:1080", device=None)

    def test_blocked_login(self, caplog):
        import logging
        from telegramBot.korail_client import BLOCKED_LOGIN_MESSAGE, ReserveHandler

        handler = ReserveHandler(egress_id="home1")
        client = Mock()
        client.login.side_effect = _block()
        with patch(
            "telegramBot.korail_client.create_korail_client", return_value=client
        ):
            with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
                assert handler.login("010-1234-5678", "pw") is False
        assert handler.loginBlocked is True
        assert handler.loginError == BLOCKED_LOGIN_MESSAGE
        client.close.assert_called_once()
        log = caplog.text
        assert "코레일 서버 차단 응답" in log
        assert "status=403" in log
        assert "code=-2000" in log
        assert "단계=로그인" in log
        assert "출구=home1" in log
        assert BLOCK_MESSAGE in log

        # 비밀번호 오류는 차단이 아님
        caplog.clear()
        client.login.side_effect = LoginFailedError("x", "WRR000101")
        with patch(
            "telegramBot.korail_client.create_korail_client", return_value=client
        ):
            with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
                assert handler.login("010-1234-5678", "pw") is False
        assert handler.loginBlocked is False
        assert "코레일 서버 차단 응답" not in caplog.text

    @pytest.mark.parametrize("stage", ["조회", "예약"])
    def test_blocked_attempt(self, stage, caplog):
        import logging
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler()
        handler.korail_client = Mock()
        train = Mock(dep_time="100000")
        if stage == "조회":
            handler.korail_client.trains.search.side_effect = _block()
        else:
            handler.korail_client.trains.search.return_value = [train]
            handler.korail_client.reservations.create.side_effect = _block()
        with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
            result = handler.reserve_single_attempt(
                "20990101", "서울", "부산", "090000"
            )
        assert result["success"] is False
        assert result["blocked"] is True
        assert f"단계={stage}" in caplog.text
        assert "출구=direct" in caplog.text

    def test_other_http_rejection_is_not_a_block(self, caplog):
        """A non-block HTTP error (e.g. 502) is retried as a normal failure, not logged as a block"""
        import logging
        from telegramBot.korail_client import ReserveHandler

        handler = ReserveHandler()
        handler.korail_client = Mock()
        handler.korail_client.trains.search.side_effect = HttpStatusError(502, "x")
        with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
            result = handler.reserve_single_attempt(
                "20990101", "서울", "부산", "090000"
            )
        assert result["success"] is False
        assert "blocked" not in result
        assert "코레일 서버 차단 응답" not in caplog.text

    @pytest.mark.parametrize("status", [403, 200])
    def test_pykorail_raises_access_restricted_for_block_body(self, status):
        """Contract with the pinned pykorail: a -2000 body raises AccessRestrictedError
        whatever the HTTP status, and normal Korail failures do not.
        Re-check this when upgrading pykorail - block detection relies on it."""
        import json
        from pykorail import Korail

        client = Korail()
        try:
            block = {"code": -2000, "id": "trace", "message": BLOCK_MESSAGE}
            with pytest.raises(AccessRestrictedError) as exc:
                client._api._parse(
                    Mock(status_code=status, text=json.dumps(block, ensure_ascii=False))
                )
            assert exc.value.status_code == status
            assert exc.value.code == "-2000"

            payload = {"strResult": "FAIL", "h_msg_cd": "WRR000101", "h_msg_txt": "x"}
            response = Mock(status_code=status, text=json.dumps(payload))
            assert client._api._parse(response) == payload
        finally:
            client.close()
