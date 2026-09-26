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
        assert reserve_handler.chatId == ""

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

    def test_attempt_reservation_stops_on_fatal_error(
        self, reserve_handler, mock_korail_client
    ):
        """Retry loop (subprocess mode) re-raises fatal errors immediately"""
        mock_korail_client.trains.search = Mock(
            side_effect=StationNotFoundError(["광주열분"])
        )
        reserve_handler.korail_client = mock_korail_client
        reserve_handler._update_reserve_info(
            "20250115", "광주열분", "부산", "090000", TrainType.KTX, "", "1200"
        )

        with pytest.raises(StationNotFoundError):
            reserve_handler._attempt_reservation()

        assert mock_korail_client.trains.search.call_count == 1

    @patch("telegramBot.korail_client.requests.session")
    @patch("telegramBot.korail_client.os.getenv")
    def test_send_reservation_status_success(
        self, mock_getenv, mock_session, reserve_handler
    ):
        """Test sendReservationStatus with success status"""
        mock_getenv.return_value = "true"  # IS_DEV=true
        mock_post = Mock()
        mock_session.return_value.post = mock_post

        reserve_handler.chatId = "123456"
        reserve_handler.reserveInfo["reserveSuc"] = True

        reserve_handler.sendReservationStatus("Train reserved successfully")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "127.0.0.1:8390" in call_args[0][0]
        assert call_args[1]["params"]["status"] == 1

    @patch("telegramBot.korail_client.requests.session")
    @patch("telegramBot.korail_client.os.getenv")
    def test_send_reservation_status_failure(
        self, mock_getenv, mock_session, reserve_handler
    ):
        """Test sendReservationStatus with failure status"""
        mock_getenv.return_value = "false"  # IS_DEV=false
        mock_post = Mock()
        mock_session.return_value.post = mock_post

        reserve_handler.chatId = "123456"
        reserve_handler.reserveInfo["reserveSuc"] = False

        reserve_handler.sendReservationStatus(None)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "127.0.0.1:8391" in call_args[0][0]
        assert call_args[1]["params"]["status"] == 0

    def test_send_bot_state_change(self, reserve_handler):
        """Test sendBotStateChange method"""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        # Mock the session's post method
        reserve_handler.s.post = Mock(return_value=mock_response)

        reserve_handler.sendBotStateChange("123456", "Test message", 1)

        reserve_handler.s.post.assert_called_once()
        call_args = reserve_handler.s.post.call_args
        assert call_args[1]["params"]["status"] == 1
        assert call_args[1]["params"]["reserveInfo"] == "Test message"

    def test_send_bot_state_change_with_retry(self, reserve_handler):
        """Test sendBotStateChange retries on failure"""
        import requests

        # Mock the session's post method to fail twice, then succeed
        reserve_handler.s.post = Mock()
        reserve_handler.s.post.side_effect = [
            requests.exceptions.RequestException("Network error"),
            requests.exceptions.RequestException("Network error"),
            Mock(),  # Third attempt succeeds
        ]

        reserve_handler.sendBotStateChange("123456", "Test message", 1)

        assert reserve_handler.s.post.call_count == 3


class TestWarpProxy:
    """Cloudflare WARP proxy wiring for Korail requests"""

    @pytest.fixture(autouse=True)
    def _clear_use_warp(self, monkeypatch):
        """Each test starts with the default USE_WARP (enabled)"""
        monkeypatch.delenv("USE_WARP", raising=False)

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, True),
            ("true", True),
            ("TRUE", True),
            ("1", True),
            ("false", False),
            ("False", False),
            (" false ", False),
            ("0", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_is_warp_enabled(self, monkeypatch, value, expected):
        """USE_WARP defaults to enabled and accepts false/0/no/off to disable"""
        from telegramBot.korail_client import is_warp_enabled

        if value is not None:
            monkeypatch.setenv("USE_WARP", value)
        assert is_warp_enabled() is expected

    def test_use_warp_false_skips_proxy(self, monkeypatch):
        """USE_WARP=false sends Korail requests directly even if WARP_PROXY_URL is set"""
        from telegramBot.korail_client import (
            check_warp_status,
            create_korail_client,
            get_warp_proxy_url,
        )

        monkeypatch.setenv("WARP_PROXY_URL", "socks5h://warp:1080")
        monkeypatch.setenv("USE_WARP", "false")

        assert get_warp_proxy_url() == ""
        assert check_warp_status() == "disabled"
        client = create_korail_client()
        try:
            assert not client._api._session.proxies
        finally:
            client.close()

    def test_create_client_uses_warp_proxy(self, monkeypatch):
        """WARP_PROXY_URL is applied to pykorail's HTTP session"""
        from telegramBot.korail_client import create_korail_client

        monkeypatch.setenv("WARP_PROXY_URL", "socks5h://warp:1080")
        client = create_korail_client()
        try:
            assert client._api._session.proxies == {
                "http": "socks5h://warp:1080",
                "https": "socks5h://warp:1080",
            }
        finally:
            client.close()

    def test_create_client_without_proxy(self, monkeypatch):
        """No proxy is set when WARP_PROXY_URL is empty"""
        from telegramBot.korail_client import create_korail_client

        monkeypatch.setenv("WARP_PROXY_URL", "")
        client = create_korail_client()
        try:
            assert not client._api._session.proxies
        finally:
            client.close()

    def test_korail_block_response_is_logged(self, monkeypatch, caplog):
        """Korail server block (code -2000) is written to the server log"""
        import json
        import logging
        from telegramBot.korail_client import create_korail_client

        monkeypatch.setenv("WARP_PROXY_URL", "socks5h://warp:1080")
        block = {
            "code": -2000,
            "id": "2c0a2515-6ea1-9bef-5dca-0f6d37da13c1",
            "message": "매크로 등 미허가 도구 사용 시 이용이 제한될 수 있습니다.",
        }
        response = Mock(
            text=json.dumps(block, ensure_ascii=False),
            url="https://smart.letskorail.com/login?mbCrdNo=1234",
        )
        client = create_korail_client()
        try:
            with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
                assert client._api._parse(response) == block
        finally:
            client.close()

        log = caplog.text
        assert "코레일 서버 차단 응답" in log
        assert "code=-2000" in log
        assert "2c0a2515-6ea1-9bef-5dca-0f6d37da13c1" in log
        assert "socks5h://warp:1080" in log
        assert "매크로 등 미허가 도구" in log
        assert "mbCrdNo" not in log  # 쿼리스트링은 남기지 않음

    def test_normal_korail_response_is_not_logged(self, monkeypatch, caplog):
        """Regular Korail responses (even failures) are not logged as blocks"""
        import json
        import logging
        from telegramBot.korail_client import create_korail_client

        monkeypatch.setenv("WARP_PROXY_URL", "")
        payload = {"strResult": "FAIL", "h_msg_cd": "WRR000101", "h_msg_txt": "x"}
        response = Mock(text=json.dumps(payload), url="https://smart.letskorail.com/x")
        client = create_korail_client()
        try:
            with caplog.at_level(logging.ERROR, logger="telegramBot.korail_client"):
                assert client._api._parse(response) == payload
        finally:
            client.close()

        assert "코레일 서버 차단 응답" not in caplog.text

    def test_check_warp_status_disabled(self, monkeypatch):
        """check_warp_status reports disabled without WARP_PROXY_URL"""
        from telegramBot.korail_client import check_warp_status

        monkeypatch.delenv("WARP_PROXY_URL", raising=False)
        assert check_warp_status() == "disabled"

    def test_check_warp_status_parses_trace(self, monkeypatch):
        """check_warp_status reads warp=... from the Cloudflare trace"""
        from telegramBot.korail_client import check_warp_status

        monkeypatch.setenv("WARP_PROXY_URL", "socks5h://warp:1080")
        response = Mock(text="fl=1\nip=104.28.0.1\nloc=KR\nwarp=on\n")
        with patch("curl_cffi.requests.get", return_value=response) as mock_get:
            assert check_warp_status() == "on"

        assert mock_get.call_args[1]["proxies"]["https"] == "socks5h://warp:1080"
        # 코레일(IPv4 전용)과 같은 IPv4 출구를 보도록 IP 주소로 확인
        assert mock_get.call_args[0][0] == "https://1.1.1.1/cdn-cgi/trace"

    def test_check_warp_status_error(self, monkeypatch):
        """check_warp_status reports proxy connection errors"""
        from telegramBot.korail_client import check_warp_status

        monkeypatch.setenv("WARP_PROXY_URL", "socks5h://warp:1080")
        with patch("curl_cffi.requests.get", side_effect=Exception("refused")):
            assert check_warp_status().startswith("error: refused")
