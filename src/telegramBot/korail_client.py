import logging
import requests
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

sys.setrecursionlimit(10**7)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 재시도해도 결과가 바뀌지 않는 오류 (역 이름 오류, 이미 지난 날짜)
FATAL_ERRORS = (StationNotFoundError, PastDepartureError)

# 코레일 서버가 매크로/비정상 환경으로 판단해 요청을 차단할 때 주는 응답 코드
# 응답 형식이 {"code": -2000, "id": ..., "message": ...} 로 일반 API 응답과 달라 pykorail 이 버림
KORAIL_BLOCKED_CODE = "-2000"

# pykorail(0.2.0) 이 서버 응답에 사유가 없을 때 넣는 기본 로그인 실패 문구
PYKORAIL_FALLBACK_LOGIN_MSG = "아이디 또는 비밀번호가 올바르지 않습니다"

BLOCKED_LOGIN_MESSAGE = (
    "코레일 서버가 요청을 일시적으로 차단했습니다. 잠시 후 다시 시도해주세요."
)


class KorailBlockedError(Exception):
    """코레일 서버 차단 응답(code -2000)

    pykorail 예외(KorailError)와 따로 두어 일반 실패(로그인 실패, 열차 없음)와 구분한다.
    차단 중에 같은 출구로 계속 요청하면 차단이 길어지므로 호출 측은 출구를 쉬게 해야 한다.
    """

    def __init__(self, block_id=None, message=None):
        super().__init__(f"코레일 서버 차단 응답 (id={block_id}): {message}")
        self.block_id = block_id
        self.message = message


def create_korail_client(proxy_url="", egress_id=""):
    """pykorail 클라이언트 생성

    Args:
        proxy_url: 코레일 요청을 보낼 프록시 (비어 있으면 직접 요청). 코레일 요청에만 적용되고
            텔레그램·내부 콜백 요청은 프록시를 거치지 않음
        egress_id: 로그에 남길 출구 이름 (프록시 주소는 인증 정보가 있을 수 있어 남기지 않음)
    """
    client = Korail()
    if proxy_url:
        # pykorail 은 프록시 옵션을 제공하지 않으므로 내부 HTTP 세션(curl_cffi)에 직접 지정.
        # pykorail 의 모든 코레일 요청은 이 세션을 거침 (client._api.get/post)
        client._api._session.proxies = {"http": proxy_url, "https": proxy_url}
    _raise_on_korail_blocks(client, egress_id or "direct")
    return client


def _raise_on_korail_blocks(client, egress_id):
    """코레일 서버 차단 응답(code -2000)을 서버 로그에 남기고 KorailBlockedError 발생

    pykorail 은 이 응답을 일반 실패(로그인 실패, 열차 없음 등)로 처리해 원본을 버리므로,
    모든 응답이 지나가는 파싱 단계에서 확인한다.
    """
    api = client._api
    parse = api._parse

    def parse_and_check(response):
        payload = parse(response)
        if str(payload.get("code")) == KORAIL_BLOCKED_CODE:
            # 조회 파라미터(회원번호 등)가 남지 않도록 쿼리스트링은 제외
            url = str(getattr(response, "url", "") or "").split("?")[0]
            logger.error(
                "코레일 서버 차단 응답 (code=%s, id=%s, url=%s, 출구=%s): %s",
                payload.get("code"),
                payload.get("id"),
                url or "unknown",
                egress_id,
                payload.get("message"),
            )
            raise KorailBlockedError(payload.get("id"), payload.get("message"))
        return payload

    api._parse = parse_and_check


class ReserveHandler:
    def __init__(self, proxy_url="", egress_id=""):
        # 코레일 요청 출구 (core.egress). 같은 계정은 항상 같은 출구를 씀
        self.proxy_url = proxy_url
        self.egress_id = egress_id
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
        try:
            client = create_korail_client(self.proxy_url, self.egress_id)
            # pykorail 은 로그인 실패 시 LoginFailedError 를 발생시킴
            client.login(username, password)
        except KorailBlockedError:
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
        """Single reservation attempt (재시도 루프는 core.runner.run_reservation 담당)

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
                재시도가 무의미한 오류(역 이름 오류, 지난 날짜)이면 'fatal': True,
                코레일 서버 차단 응답이면 'blocked': True 가 추가됨
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

        except KorailBlockedError as e:
            return {"success": False, "result": None, "error": str(e), "blocked": True}
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
