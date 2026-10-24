import logging
import requests
import sys
from datetime import datetime, timedelta, timezone
from pykorail import Korail
from pykorail.device import profile_by_id
from pykorail import (
    AccessRestrictedError,
    HttpStatusError,
    KorailError,
    NeedToLoginError,
    PasswordChangeRequiredError,
    ReserveOption,
    TrainType,
    SoldOutError,
    NoResultsError,
    PastDepartureError,
    StationNotFoundError,
)

sys.setrecursionlimit(10**7)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 재시도해도 결과가 바뀌지 않는 오류 (역 이름 오류, 이미 지난 날짜)
FATAL_ERRORS = (StationNotFoundError, PastDepartureError)

# 좌석이 없을 때 예약대기를 신청하는 좌석 옵션
# 코레일 조회 결과의 예약대기 여부(h_wait_rsv_flg)는 일반실 기준이므로 일반실 대기만 신청하고,
# 특실만 원하는 경우(SPECIAL_ONLY)는 대기를 걸지 않는다
WAITLIST_OPTIONS = (
    ReserveOption.GENERAL_FIRST,
    ReserveOption.GENERAL_ONLY,
    ReserveOption.SPECIAL_FIRST,
)

# pykorail 이 서버 응답에 사유가 없을 때 넣는 기본 로그인 실패 문구
PYKORAIL_FALLBACK_LOGIN_MSG = "아이디 또는 비밀번호가 올바르지 않습니다"

BLOCKED_LOGIN_MESSAGE = (
    "코레일 서버가 요청을 일시적으로 차단했습니다. 잠시 후 다시 시도해주세요."
)

# 한 번의 시도에서 이어 받을 조회 결과 쪽 수 상한 (코레일은 한 번에 보통 10편만 줌)
MAX_SEARCH_PAGES = 10

PASSWORD_CHANGE_MESSAGE = (
    "코레일에서 비밀번호 변경 안내를 처리해야 로그인할 수 있습니다. "
    "코레일톡 앱이나 홈페이지에서 안내를 처리한 뒤 다시 시도해주세요."
)


def create_korail_client(proxy_url="", *, device=None):
    """pykorail 클라이언트 생성

    코레일 서버 차단 응답(code -2000)은 pykorail 이 AccessRestrictedError 로 올리므로
    (HTTP 상태와 무관) 호출 측에서 일반 실패(로그인 실패, 열차 없음)와 구분해 잡는다.
    차단 중에 같은 출구로 계속 요청하면 차단이 길어지므로 호출 측은 출구를 쉬게 해야 한다.

    Args:
        proxy_url: 코레일 요청을 보낼 프록시 (비어 있으면 직접 요청). 코레일 요청에만 적용되고
            텔레그램·내부 콜백 요청은 프록시를 거치지 않음
        device: ``{"profile_id", "android_id"}`` (UserService.korail_device). 없으면 pykorail 이
            클라이언트마다 새 Android ID를 만들어 로그인할 때마다 다른 기기로 보이므로 항상 넘긴다.
    """
    client = Korail(**_device_kwargs(device))
    if proxy_url:
        # pykorail 은 프록시 옵션을 제공하지 않으므로 내부 HTTP 세션(curl_cffi)에 직접 지정.
        # pykorail 의 모든 코레일 요청은 이 세션을 거침 (client._api.get/post)
        _http_session(client).proxies = {"http": proxy_url, "https": proxy_url}
    return client


def _http_session(client):
    """pykorail 이 코레일 요청에 쓰는 curl_cffi 세션

    0.4.0 부터 ``client._api._session`` 은 curl_cffi 세션을 감싼 IdempotentSession 이라
    거기에 proxies 를 넣으면 아무 효과 없이 직접 요청으로 나감. 구조가 또 바뀌면 조용히
    직접 요청으로 나가지 않도록 curl_cffi 세션이 아니면 예외를 발생시킨다.
    """
    session = client._api._session._session
    if type(session).__module__.split(".")[0] != "curl_cffi":
        raise RuntimeError(
            f"pykorail 내부 HTTP 세션이 curl_cffi Session 이 아님: {type(session)!r}"
        )
    return session


def _device_kwargs(device):
    if not device or not device.get("android_id"):
        return {}
    android_id = device["android_id"]
    profile = profile_by_id(device.get("profile_id"), android_id=android_id)
    if profile is None:
        # pykorail 카탈로그에서 사라진 모델 - Android ID만 유지 (모델은 pykorail 기본값)
        return {"android_id": android_id}
    return {"device_profile": profile}


def _train_key(train):
    """조회 쪽 사이에서 같은 열차를 알아보는 키"""
    return (train.train_no, train.dep_date, train.dep_time)


def _departure(train):
    """열차의 출발 일시 (KST). 읽을 수 없으면 None (다음 쪽을 받지 않음)"""
    try:
        return datetime.strptime(
            train.dep_date + train.dep_time, "%Y%m%d%H%M%S"
        ).replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def _log_block(error, egress_id, stage):
    """코레일 서버 차단 응답을 서버 로그에 남김 (출구별 차단 빈도·원인 추적용)"""
    logger.error(
        "코레일 서버 차단 응답 (status=%s, code=%s, 단계=%s, 출구=%s): %s",
        error.status_code,
        error.code,
        stage,
        egress_id or "direct",
        error.msg,
    )


class _PagingStopped(Exception):
    """다음 쪽을 받기 전에 멈춰야 함 (취소, 최대 실행 시간 등 - before_page 가 False)"""


class ReserveHandler:
    def __init__(self, proxy_url="", egress_id="", *, device=None):
        # 코레일 요청 출구 (core.egress). 같은 계정은 항상 같은 출구를 씀
        self.proxy_url = proxy_url
        self.egress_id = egress_id
        # 코레일 접속 기기 신원 - 재로그인해도 같은 기기로 접속 (create_korail_client 참고)
        self.device = device
        self.korail_client = None
        self.username = ""
        self.password = ""
        self.s = requests.session()
        self.reserveInfo = {
            "depDate": "",
            "depTime": "",
            "srcLocate": "",
            "dstLocate": "",
            "special": "",
            "reserveSuc": False,
        }
        self.interval = 1  # sec 분당 100회 이상이면 이상탐지에 걸림
        self.loginSuc = False
        self.loginError = ""  # 사용자에게 보여줄 마지막 로그인 실패 사유
        self.loginBlocked = False  # 마지막 로그인이 코레일 차단 응답으로 실패했는지
        # 마지막 로그인이 비밀번호 변경 안내로 막혔는지 (비밀번호 오류가 아님)
        self.loginPasswordChange = False
        self.txtGoHour = "000000"
        self.specialVal = ""

        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Upgrade-Insecure-Requests": "1",
                "Referer": "http://www.letskorail.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "cross-site",
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": "http://www.letskorail.com",
            }
        )

    def login(self, username, password):
        client = None
        self.loginBlocked = False
        self.loginPasswordChange = False
        try:
            client = create_korail_client(self.proxy_url, device=self.device)
            # pykorail 은 로그인 실패 시 LoginFailedError 를 발생시킴
            client.login(username, password)
        except AccessRestrictedError as e:
            _log_block(e, self.egress_id, "로그인")
            if client is not None:
                client.close()
            self.loginSuc = False
            self.loginBlocked = True
            self.loginError = BLOCKED_LOGIN_MESSAGE
            return False
        except Exception as e:
            print(f"Login failed with exception: {e}")
            if client is not None:
                client.close()
            self.loginSuc = False
            self.loginPasswordChange = isinstance(e, PasswordChangeRequiredError)
            self.loginError = self._login_error_message(e)
            return False

        # 재로그인 성공 시에만 기존 세션을 교체
        self.close()
        self.korail_client = client
        self.username = username
        self.password = password
        self.loginSuc = True
        self.loginError = ""
        return True

    @staticmethod
    def _login_error_message(error):
        """로그인 실패 사유 (코레일이 준 안내 메시지, 없으면 일반 안내)

        str(KorailError) 는 "메시지 (코드)" 형식이라 코드가 없으면 "(None)" 이 붙으므로 msg 만 사용
        """
        if isinstance(error, PasswordChangeRequiredError):
            # 아이디·비밀번호는 맞지만 6개월 넘게 같은 비밀번호를 써서 변경 안내에 걸림
            # (같은 비밀번호로 다시 시도해도 결과가 같음)
            return PASSWORD_CHANGE_MESSAGE
        if isinstance(error, HttpStatusError):
            # 자격증명을 보기 전에 서버가 요청을 거절함 (502 등) - 비밀번호 문제가 아님
            reason = error.msg or f"HTTP {error.status_code}"
            return f"코레일 서버가 요청을 거절했습니다: {reason}"
        if isinstance(error, KorailError) and error.msg:
            # pykorail 은 서버가 사유(h_msg_txt)와 코드 없이 거부하면 이 문구를 대신 넣음
            # 실제 비밀번호 오류는 서버가 사유와 코드(WRR000101 등)를 주므로 구분해서 안내
            if error.code is None and error.msg == PYKORAIL_FALLBACK_LOGIN_MSG:
                return "코레일 서버가 사유 없이 로그인을 거부했습니다. 잠시 후 다시 시도해주세요."
            return error.msg
        return "코레일 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요."

    def close(self):
        """코레일 HTTP 세션 정리"""
        if self.korail_client is not None:
            try:
                self.korail_client.close()
            except Exception as e:
                print(f"Failed to close korail client: {e}")
            self.korail_client = None

    def reserve_single_attempt(
        self,
        depDate,
        srcLocate,
        dstLocate,
        depTime="000000",
        trainType=TrainType.KTX,
        special=ReserveOption.GENERAL_FIRST,
        maxDepTime="2400",
        allowWaitlist=False,
        beforePage=None,
    ):
        """Single reservation attempt (재시도 루프는 core.runner.run_reservation 담당)

        Args:
            depDate (str): 출발 날짜, 형식은 'YYYYMMDD'.
            srcLocate (str): 출발지 코드.
            dstLocate (str): 도착지 코드.
            depTime (str, optional): 출발 시간, 형식은 'HHMMSS'. 기본값은 "000000".
            trainType (TrainType, optional): 예약할 기차 유형. 기본값은 TrainType.KTX.
            special (ReserveOption, optional): 예약 옵션. 기본값은 ReserveOption.GENERAL_FIRST.
            maxDepTime (str, optional): 최대 출발 시간, 형식은 'HHMM'. 기본값은 "2400".
            allowWaitlist (bool, optional): 모두 매진일 때 예약대기 신청 여부. 기본값은 False.
            beforePage (callable, optional): 둘째 쪽부터 조회 요청 전에 호출. False 를
                반환하면 이번 시도를 멈춤 (호출 측이 출구별 요청 간격·취소를 확인하는 자리)

        조회 결과는 한 쪽(보통 10편)씩 받으므로, 범위 안의 열차를 다 볼 때까지 다음 쪽을
        이어 받는다 (최대 MAX_SEARCH_PAGES 쪽). 쪽마다 좌석이 있는 열차를 먼저 예약하고,
        allowWaitlist 이고 범위 안의 열차가 모두 매진이면 예약대기가 열린 가장 이른 열차에
        (일반실) 예약대기를 신청한다 (특실만 예약 옵션 제외).

        Returns:
            dict: {'success': bool, 'result': reservation_object_or_none, 'error': str_or_none}
                재시도가 무의미한 오류(역 이름 오류, 지난 날짜)이면 'fatal': True,
                코레일 서버 차단 응답이면 'blocked': True,
                예약대기를 신청했으면 'waiting': True,
                코레일 세션이 끊겼으면(P058) 'needs_login': True,
                beforePage 가 멈추라고 하면 'stopped': True 가 추가됨
        """
        self._update_reserve_info(
            depDate, srcLocate, dstLocate, depTime, trainType, special, maxDepTime
        )
        waitlist = allowWaitlist and special in WAITLIST_OPTIONS

        stage = "조회"
        try:
            found = False
            waitlist_trains = []
            for trains in self._search_pages(beforePage):
                found = True
                stage = "예약"
                # 좌석이 있는 열차부터 (출발 시각 순)
                for train in (t for t in trains if t.has_seat()):
                    print(f"열차 발견 : {train} <- 에 대한 예약을 시작합니다.")
                    result = self._attempt(train, self._try_reserve)
                    if result:
                        return result
                if waitlist:
                    waitlist_trains.extend(t for t in trains if self._waitlist_open(t))
                stage = "조회"

            if not found:
                return {
                    "success": False,
                    "result": None,
                    "error": "No trains available",
                }

            # 범위 안의 열차가 모두 매진이면 예약대기가 열린 열차에 대기 신청
            stage = "예약"
            for train in waitlist_trains:
                print(f"예약대기 가능 : {train} <- 에 예약대기를 신청합니다.")
                result = self._attempt(train, self._try_waitlist, waiting=True)
                if result:
                    return result

            return {"success": False, "result": None, "error": "All trains sold out"}

        except _PagingStopped:
            return {"success": False, "result": None, "error": None, "stopped": True}
        except AccessRestrictedError as e:
            _log_block(e, self.egress_id, stage)
            return {"success": False, "result": None, "error": str(e), "blocked": True}
        except FATAL_ERRORS as e:
            return {"success": False, "result": None, "error": str(e), "fatal": True}
        except NeedToLoginError as e:
            # 서버 메시지("로그아웃되었습니다" 등)에 로그인이라는 말이 없을 수 있어 타입으로 구분
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "needs_login": True,
            }
        except Exception as e:
            error_str = str(e)
            # Check for duplicate reservation at top level too
            if "동일한 예약 내역이 있으니" in error_str or "WRR800029" in error_str:
                self.reserveInfo["reserveSuc"] = True
                return {
                    "success": True,
                    "result": "duplicate_reservation",
                    "error": None,
                }
            return {"success": False, "result": None, "error": error_str}

    def _attempt(self, train, reserve, waiting=False):
        """열차 하나 예약 시도. 성공하면 결과 dict, 매진이면 None

        waiting: 예약대기 신청 경로 - 이미 같은 대기가 있다는 응답도 예약대기로 보고해야
        "좌석 확보, 20분 안에 결제" 로 잘못 안내되지 않음
        """
        try:
            reservation = reserve(train)
        except SoldOutError:
            print("예약을 놓쳤습니다. 다음 열차를 찾습니다.")
            return None
        except Exception as e:
            error_str = str(e)
            # 이미 같은 예약(또는 예약대기)이 있음 - 사실상 성공
            if "동일한 예약 내역이 있으니" in error_str or "WRR800029" in error_str:
                self.reserveInfo["reserveSuc"] = True
                return {
                    "success": True,
                    "result": "duplicate_reservation",
                    "error": None,
                    "waiting": waiting,
                }
            raise
        if not reservation:
            return None
        self.reserveInfo["reserveSuc"] = True
        return {
            "success": True,
            "result": reservation,
            "error": None,
            "waiting": getattr(reservation, "is_waiting", False) is True,
        }

    @staticmethod
    def _waitlist_open(train):
        return not train.has_seat() and train.has_waiting_list()

    def _update_reserve_info(
        self, depDate, srcLocate, dstLocate, depTime, trainType, special, maxDepTime
    ):
        self.reserveInfo.update(
            {
                "depDate": depDate,
                "srcLocate": srcLocate,
                "dstLocate": dstLocate,
                "depTime": depTime,
                "trainType": trainType,
                "special": special,
                "maxDepTime": maxDepTime,
            }
        )

    def _search_pages(self, before_page=None):
        """조회 범위 안의 열차를 출발 시각 순으로 한 쪽씩 돌려줌

        코레일은 조회 한 번에 한 쪽(보통 10편)만 주므로, 쪽의 마지막 열차 출발 시각부터
        다시 조회해 이어 받는다 (앱의 다음 쪽 조회와 같은 방식이라 경계의 열차가 다시
        나오므로 열차 단위로 거름). 최대 출발 시간(HHMM)을 넘는 열차가 나오거나 새 열차가
        없거나 MAX_SEARCH_PAGES 쪽을 받으면 멈춘다.

        다음 쪽 기준 시각을 알려면 쪽 전체가 필요하므로 매진 열차도 받는다
        (include_no_seats - 좌석·예약대기 여부는 호출 측이 고름).

        Raises:
            _PagingStopped: before_page 가 다음 쪽을 받지 말라고 함
        """
        maxDepTime = int(self.reserveInfo["maxDepTime"])
        cursor = self._depart_after()
        seen = set()
        for page_no in range(MAX_SEARCH_PAGES):
            if page_no:
                if before_page is not None and not before_page():
                    raise _PagingStopped()
                # 다음 쪽 차례를 기다리는 동안 그 시각이 지났으면 pykorail 이 PastDepartureError
                # (재시도 없이 실패)로 거부하므로 현재 시각보다 이르지 않게 함
                cursor = max(cursor, datetime.now(KST))
            try:
                page = self.korail_client.trains.search(
                    self.reserveInfo["srcLocate"],
                    self.reserveInfo["dstLocate"],
                    depart_after=cursor,
                    train_type=self.reserveInfo["trainType"],
                    include_no_seats=True,
                )
            except NoResultsError:
                return
            fresh = [train for train in page if _train_key(train) not in seen]
            if not fresh:
                return
            seen.update(_train_key(train) for train in fresh)
            in_range = [
                train for train in fresh if int(train.dep_time[:4]) < maxDepTime
            ]
            if in_range:
                yield in_range
            following = _departure(page[-1])
            if len(in_range) < len(fresh) or following is None or following <= cursor:
                return
            cursor = following

    def _depart_after(self):
        """열차 검색 기준 시각 (KST)

        pykorail 은 이미 지난 시각으로 검색하면 PastDepartureError 를 발생시키므로,
        출발일이 오늘이고 시작 시각이 지났다면 현재 시각부터 검색한다.
        출발일 자체가 지난 경우에는 그대로 넘겨 PastDepartureError 로 중단되게 한다.
        """
        requested = datetime.strptime(
            f"{self.reserveInfo['depDate']}{self.reserveInfo['depTime']}",
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=KST)
        now = datetime.now(KST)
        if requested < now and requested.date() == now.date():
            return now
        return requested

    def _try_reserve(self, train):
        try:
            return self.korail_client.reservations.create(
                train, option=self.reserveInfo["special"]
            )
        except SoldOutError:
            print("예약을 놓쳤습니다. 다음 열차를 찾습니다.")
            return None

    def _try_waitlist(self, train):
        """좌석이 없는 열차에 일반실 예약대기 신청 (pykorail 이 좌석 없는 열차는 대기로 예약)

        조회 결과의 대기 가능 여부가 일반실 기준이므로 옵션과 관계없이 일반실로 신청한다.
        """
        return self.korail_client.reservations.create(
            train, option=ReserveOption.GENERAL_ONLY
        )
