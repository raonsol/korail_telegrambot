"""
Unit tests for Korail API client (korail_client.py)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from korail2 import TrainType, ReserveOption, SoldOutError, NoResultsError


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

    def test_login_failure(self, reserve_handler):
        """Test failed login"""
        mock_client = Mock()
        mock_client.login = Mock(return_value=False)

        with patch("telegramBot.korail_client.Korail", return_value=mock_client):
            result = reserve_handler.login("test_user", "wrong_password")

            assert result is False
            assert reserve_handler.loginSuc is False

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
        mock_korail_client.search_train = Mock(return_value=[sample_train_data])
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "1200",
        }

        # Mock train __str__ method
        sample_train_data.__str__ = Mock(return_value="KTX 001(09:00~11:30)")

        trains = reserve_handler._search_trains()

        assert len(trains) == 1
        assert trains[0] == sample_train_data

    def test_search_trains_no_results(self, reserve_handler, mock_korail_client):
        """Test train search with no results"""
        mock_korail_client.search_train = Mock(side_effect=NoResultsError())
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
        mock_korail_client.search_train = Mock(return_value=[sample_train_data])
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {
            "srcLocate": "서울",
            "dstLocate": "부산",
            "depDate": "20250115",
            "depTime": "090000",
            "trainType": TrainType.KTX,
            "maxDepTime": "0800",  # Earlier than train departure
        }

        # Mock train __str__ method
        sample_train_data.__str__ = Mock(return_value="KTX 001(09:00~11:30)")

        trains = reserve_handler._search_trains()

        assert trains == []

    def test_try_reserve_success(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test successful reservation attempt"""
        mock_reservation = Mock()
        mock_korail_client.reserve = Mock(return_value=mock_reservation)
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {"special": ReserveOption.GENERAL_FIRST}

        result = reserve_handler._try_reserve(sample_train_data)

        assert result == mock_reservation
        mock_korail_client.reserve.assert_called_once_with(
            sample_train_data, option=ReserveOption.GENERAL_FIRST
        )

    def test_try_reserve_sold_out(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reservation attempt when sold out"""
        mock_korail_client.reserve = Mock(side_effect=SoldOutError())
        reserve_handler.korail_client = mock_korail_client
        reserve_handler.reserveInfo = {"special": ReserveOption.GENERAL_FIRST}

        result = reserve_handler._try_reserve(sample_train_data)

        assert result is None

    def test_reserve_single_attempt_success(
        self, reserve_handler, mock_korail_client, sample_train_data
    ):
        """Test reserve_single_attempt with successful reservation"""
        mock_reservation = Mock()
        mock_korail_client.search_train = Mock(return_value=[sample_train_data])
        mock_korail_client.reserve = Mock(return_value=mock_reservation)
        reserve_handler.korail_client = mock_korail_client

        # Mock train __str__ method
        sample_train_data.__str__ = Mock(return_value="KTX 001(09:00~11:30)")

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
        mock_korail_client.search_train = Mock(return_value=[])
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
        mock_korail_client.search_train = Mock(return_value=[sample_train_data])
        mock_korail_client.reserve = Mock(side_effect=SoldOutError())
        reserve_handler.korail_client = mock_korail_client

        # Mock train __str__ method
        sample_train_data.__str__ = Mock(return_value="KTX 001(09:00~11:30)")

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
        mock_korail_client.search_train = Mock(return_value=[sample_train_data])
        mock_korail_client.reserve = Mock(
            side_effect=Exception("동일한 예약 내역이 있으니 확인하시기 바랍니다")
        )
        reserve_handler.korail_client = mock_korail_client

        # Mock train __str__ method
        sample_train_data.__str__ = Mock(return_value="KTX 001(09:00~11:30)")

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
