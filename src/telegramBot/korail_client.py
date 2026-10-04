import json
import logging
import requests
import sys
from datetime import datetime, timedelta, timezone
from pykorail import Korail
from pykorail import (
    HttpStatusError,
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
# 응답 형식이 {"code": -2000, "id": ..., "message": ...} 로 일반 API 응답과 다름
# pykorail 0.2.1 부터 HTTP 4xx·5xx(차단은 403)이면 HttpStatusError 로 올라오고 id 는 버려짐
KORAIL_BLOCKED_CODE = "-2000"

# pykorail 이 서버 응답에 사유가 없을 때 넣는 기본 로그인 실패 문구
PYKORAIL_FALLBACK_LOGIN_MSG = "아이디 또는 비밀번호가 올바르지 않습니다"


def create_korail_client():
    """pykorail 클라이언트 생성 (코레일 서버 차단 응답을 로그로 남기도록 설정)"""
    client = Korail()
    _log_korail_blocks(client)
    return client


def _log_korail_blocks(client):
    """코레일 서버 차단 응답(code -2000)을 서버 로그에 남김

    pykorail 은 차단 응답을 HttpStatusError(403) 또는 일반 실패(로그인 실패, 열차 없음 등)로
    처리해 원본(요청 id 등)을 버리므로, 모든 응답이 지나가는 파싱 단계에서 확인한다.
    """
    api = client._api
    parse = api._parse

    def parse_and_log(response):
        try:
            payload = parse(response)
        except HttpStatusError:
            _log_if_blocked(response, _json_body(response))
            raise
        _log_if_blocked(response, payload)
        return payload

    api._parse = parse_and_log


def _json_body(response):
    try:
        body = json.loads(response.text)
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _log_if_blocked(response, payload):
    if str(payload.get("code")) != KORAIL_BLOCKED_CODE:
        return
    # 조회 파라미터(회원번호 등)가 남지 않도록 쿼리스트링은 제외
    url = str(getattr(response, "url", "") or "").split("?")[0]
    logger.error(
        "코레일 서버 차단 응답 (status=%s, code=%s, id=%s, url=%s): %s",
        getattr(response, "status_code", None),
        payload.get("code"),
        payload.get("id"),
        url or "unknown",
        payload.get("message"),
    )


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
        if isinstance(error, HttpStatusError):
            # 자격증명을 보기 전에 서버가 요청을 거절함 (403 이용제한 등) - 비밀번호 문제가 아님
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
