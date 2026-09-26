import os
import logging
import requests
import time
import sys
from datetime import datetime, timedelta, timezone
from pykorail import Korail
from pykorail import (
    KorailError,
    ReserveOption,
    TrainType,
    SoldOutError,
    NoResultsError,
    PastDepartureError,
    StationNotFoundError,
)
from .messages import Messages

sys.setrecursionlimit(10**7)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 재시도해도 결과가 바뀌지 않는 오류 (역 이름 오류, 이미 지난 날짜)
FATAL_ERRORS = (StationNotFoundError, PastDepartureError)

WARP_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"

# 코레일 서버가 매크로/비정상 환경으로 판단해 요청을 차단할 때 주는 응답 코드
# 응답 형식이 {"code": -2000, "id": ..., "message": ...} 로 일반 API 응답과 달라 pykorail 이 버림
KORAIL_BLOCKED_CODE = "-2000"

# pykorail(0.2.0) 이 서버 응답에 사유가 없을 때 넣는 기본 로그인 실패 문구
PYKORAIL_FALLBACK_LOGIN_MSG = "아이디 또는 비밀번호가 올바르지 않습니다"


def is_warp_enabled():
    """USE_WARP 토글 (기본값 true). false/0/no/off 이면 WARP 를 사용하지 않음"""
    return os.getenv("USE_WARP", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def get_warp_proxy_url():
    """Cloudflare WARP 프록시 주소 (예: socks5h://127.0.0.1:40000)

    USE_WARP=false 이거나 WARP_PROXY_URL 이 비어 있으면 빈 문자열 (코레일에 직접 요청)
    """
    if not is_warp_enabled():
        return ""
    return os.getenv("WARP_PROXY_URL", "").strip()


def create_korail_client():
    """pykorail 클라이언트 생성. WARP_PROXY_URL 이 설정되어 있으면 코레일 요청을 WARP 로 보냄"""
    client = Korail()
    proxy_url = get_warp_proxy_url()
    if proxy_url:
        # pykorail 은 프록시 옵션을 제공하지 않으므로 내부 HTTP 세션(curl_cffi)에 직접 지정.
        # 텔레그램, 콜백 등 다른 요청은 프록시를 거치지 않음
        client._api._session.proxies = {"http": proxy_url, "https": proxy_url}
    _log_korail_blocks(client, proxy_url)
    return client


def _log_korail_blocks(client, proxy_url):
    """코레일 서버 차단 응답(code -2000)을 서버 로그에 남김

    pykorail 은 이 응답을 일반 실패(로그인 실패, 열차 없음 등)로 처리해 원본을 버리므로,
    모든 응답이 지나가는 파싱 단계에서 확인한다.
    """
    api = client._api
    parse = api._parse

    def parse_and_log(response):
        payload = parse(response)
        if str(payload.get("code")) == KORAIL_BLOCKED_CODE:
            # 조회 파라미터(회원번호 등)가 남지 않도록 쿼리스트링은 제외
            url = str(getattr(response, "url", "") or "").split("?")[0]
            logger.error(
                "코레일 서버 차단 응답 (code=%s, id=%s, url=%s, 경유=%s): %s",
                payload.get("code"),
                payload.get("id"),
                url or "unknown",
                proxy_url or "직접 요청",
                payload.get("message"),
            )
        return payload

    api._parse = parse_and_log


def check_warp_status(timeout=5):
    """WARP 프록시를 거친 요청이 실제로 Cloudflare WARP 로 나가는지 확인

    Returns:
        str: "on"/"plus" (WARP 사용 중), "off" (프록시는 되지만 WARP 아님),
             "disabled" (USE_WARP=false 또는 WARP_PROXY_URL 미설정),
             "error: ..." (프록시 연결 실패)
    """
    proxy_url = get_warp_proxy_url()
    if not proxy_url:
        return "disabled"
    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            WARP_TRACE_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
        )
        trace = dict(
            line.split("=", 1) for line in response.text.splitlines() if "=" in line
        )
        return trace.get("warp", "unknown")
    except Exception as e:
        return f"error: {e}"


class ReserveHandler:
    def __init__(self):
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
        self.txtGoHour = "000000"
        self.specialVal = ""
        self.chatId = ""  # Telegram Chat bot에서 callback 받을때 전달 받아야 함

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
        try:
            client = create_korail_client()
            # pykorail 은 로그인 실패 시 LoginFailedError 를 발생시킴
            client.login(username, password)
        except Exception as e:
            print(f"Login failed with exception: {e}")
            if client is not None:
                client.close()
            self.loginSuc = False
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

    def reserve(
        self,
        depDate,
        srcLocate,
        dstLocate,
        depTime="000000",
        trainType=TrainType.KTX,
        special=ReserveOption.GENERAL_FIRST,
        chatId="",
        maxDepTime="2400",
    ):
        """코레일 홈페이지로 기차표 예약을 시도

        Args:
            depDate (str): 출발 날짜, 형식은 'YYYYMMDD'.
            srcLocate (str): 출발지 코드.
            dstLocate (str): 도착지 코드.
            depTime (str, optional): 출발 시간, 형식은 'HHMMSS'. 기본값은 "000000".
            trainType (TrainType, optional): 예약할 기차 유형. 기본값은 TrainType.KTX.
            special (ReserveOption, optional): 예약 옵션 (예: 일반석, 일등석). 기본값은 ReserveOption.GENERAL_FIRST.
            chatId (str, optional): 예약 상태 업데이트를 전송할 채팅 ID. 기본값은 빈 문자열.
            maxDepTime (str, optional): 최대 출발 시간, 형식은 'HHMM'. 기본값은 "2400".

        Returns:
            bool: 예약이 성공하면 True, 그렇지 않으면 False.
        """
        self._update_reserve_info(
            depDate, srcLocate, dstLocate, depTime, trainType, special, maxDepTime
        )
        self.chatId = chatId
        currentTime = time.strftime("%H:%M:%S", time.localtime(time.time()))
        print(f"{currentTime} {self.reserveInfo} 작업 시작")

        reserveOne = self._attempt_reservation()

        if self.chatId:
            self.sendReservationStatus(reserveOne)
        return reserveOne

    def reserve_single_attempt(
        self,
        depDate,
        srcLocate,
        dstLocate,
        depTime="000000",
        trainType=TrainType.KTX,
        special=ReserveOption.GENERAL_FIRST,
        maxDepTime="2400",
    ):
        """Single reservation attempt for Celery mode (no retry loop)

        Args:
            depDate (str): 출발 날짜, 형식은 'YYYYMMDD'.
            srcLocate (str): 출발지 코드.
            dstLocate (str): 도착지 코드.
            depTime (str, optional): 출발 시간, 형식은 'HHMMSS'. 기본값은 "000000".
            trainType (TrainType, optional): 예약할 기차 유형. 기본값은 TrainType.KTX.
            special (ReserveOption, optional): 예약 옵션. 기본값은 ReserveOption.GENERAL_FIRST.
            maxDepTime (str, optional): 최대 출발 시간, 형식은 'HHMM'. 기본값은 "2400".

        Returns:
            dict: {'success': bool, 'result': reservation_object_or_none, 'error': str_or_none}
                재시도가 무의미한 오류(역 이름 오류, 지난 날짜)이면 'fatal': True 가 추가됨
        """
        self._update_reserve_info(
            depDate, srcLocate, dstLocate, depTime, trainType, special, maxDepTime
        )

        try:
            # Search for available trains
            trains = self._search_trains()
            if not trains:
                return {
                    "success": False,
                    "result": None,
                    "error": "No trains available",
                }

            # Try to reserve the first available train
            for train in trains:
                print(f"열차 발견 : {train} <- 에 대한 예약을 시작합니다.")
                try:
                    reservation = self._try_reserve(train)
                    if reservation:
                        self.reserveInfo["reserveSuc"] = True
                        return {"success": True, "result": reservation, "error": None}
                except SoldOutError:
                    print("예약을 놓쳤습니다. 다음 열차를 찾습니다.")
                    continue
                except Exception as e:
                    error_str = str(e)
                    # Check for duplicate reservation (which is actually success)
                    if (
                        "동일한 예약 내역이 있으니" in error_str
                        or "WRR800029" in error_str
                    ):
                        self.reserveInfo["reserveSuc"] = True
                        return {
                            "success": True,
                            "result": "duplicate_reservation",
                            "error": None,
                        }
                    # Re-raise other exceptions
                    raise

            return {"success": False, "result": None, "error": "All trains sold out"}

        except FATAL_ERRORS as e:
            return {"success": False, "result": None, "error": str(e), "fatal": True}
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

    def _attempt_reservation(self):
        reserveOne = None
        max_attempts = 1000  # 최대 시도 횟수
        attempt_count = 0
        last_error_time = time.time()
        error_count = 0

        while not reserveOne and attempt_count < max_attempts:
            try:
                trains = self._search_trains()
                for train in trains:
                    print(f"열차 발견 : {train} <- 에 대한 예약을 시작합니다.")
                    reserveOne = self._try_reserve(train)
                    if reserveOne:
                        self.reserveInfo["reserveSuc"] = True
                        break

                # 에러 카운트 리셋
                if (
                    time.time() - last_error_time > 300
                ):  # 5분 이상 에러가 없으면 카운트 리셋
                    error_count = 0

                attempt_count += 1
                time.sleep(self.interval)

            except FATAL_ERRORS:
                raise
            except Exception as e:
                error_count += 1
                last_error_time = time.time()
                print(f"예약 시도 중 오류 발생: {str(e)}")

                # 연속 에러가 10회 이상 발생하면 세션 재로그인
                if error_count >= 10:
                    print("연속 에러 발생으로 세션 재로그인 시도")
                    try:
                        self.login(self.username, self.password)
                        error_count = 0
                    except Exception as login_error:
                        print(f"세션 재로그인 실패: {str(login_error)}")
                        if self.chatId:
                            self.sendBotStateChange(
                                self.chatId,
                                "세션 오류로 인해 예약이 중단되었습니다.",
                                0,
                            )
                        raise

                time.sleep(self.interval * 2)  # 에러 발생시 대기 시간 증가

        if not reserveOne:
            print(f"최대 시도 횟수({max_attempts})를 초과했습니다.")
            if self.chatId:
                self.sendBotStateChange(
                    self.chatId, "최대 시도 횟수를 초과하여 예약이 중단되었습니다.", 0
                )

        return reserveOne

    def _search_trains(self):
        try:
            trains = self.korail_client.trains.search(
                self.reserveInfo["srcLocate"],
                self.reserveInfo["dstLocate"],
                depart_after=self._depart_after(),
                train_type=self.reserveInfo["trainType"],
            )
        except NoResultsError:
            return []
        # 최대 출발 시간(HHMM) 이전에 출발하는 열차만 남김
        maxDepTime = int(self.reserveInfo["maxDepTime"])
        return [train for train in trains if int(train.dep_time[:4]) < maxDepTime]

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

    def sendReservationStatus(self, reserveInfo):
        result = self.reserveInfo["reserveSuc"]

        if result == "wrong":
            status = -1  # Error status
        elif result:
            status = 1  # Success status
        else:
            status = 0  # Failed status

        port = 8390 if os.getenv("IS_DEV", "false") == "true" else 8391
        # port = 8391
        callbackUrl = f"http://127.0.0.1:{port}/completion/{self.chatId}"
        print(self.chatId, reserveInfo, status)
        param = {"status": status, "reserveInfo": str(reserveInfo)}
        s = requests.session()
        s.post(callbackUrl, params=param, verify=False)
        return None

    def sendBotStateChange(self, chatId, msg, status):
        try:
            port = 8390 if os.getenv("IS_DEV", "false") == "true" else 8391
            callbackUrl = f"http://127.0.0.1:{port}/completion/{chatId}"
            param = {"status": status, "reserveInfo": msg}

            # 최대 3번까지 재시도
            for attempt in range(3):
                try:
                    response = self.s.post(
                        callbackUrl, params=param, verify=False, timeout=5
                    )
                    response.raise_for_status()
                    return
                except requests.exceptions.RequestException as e:
                    if attempt == 2:  # 마지막 시도에서도 실패
                        print(f"상태 변경 메시지 전송 실패: {str(e)}")
                    time.sleep(1)  # 재시도 전 대기
        except Exception as e:
            print(f"상태 변경 메시지 전송 중 오류 발생: {str(e)}")
