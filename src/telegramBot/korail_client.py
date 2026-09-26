import requests
import sys
from korail2 import Korail
from korail2 import ReserveOption, TrainType, SoldOutError, NoResultsError

sys.setrecursionlimit(10**7)


class ReserveHandler:
    def __init__(self):
        self.korail_client = None
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
        try:
            self.korail_client = Korail(username, password, auto_login=False)
            self.loginSuc = self.korail_client.login()
            return self.loginSuc
        except Exception as e:
            print(f"Login failed with exception: {e}")
            self.loginSuc = False
            return False

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
            trains = self.korail_client.search_train(
                self.reserveInfo["srcLocate"],
                self.reserveInfo["dstLocate"],
                self.reserveInfo["depDate"],
                self.reserveInfo["depTime"],
                train_type=self.reserveInfo["trainType"],
            )
            if trains:  # Check if trains list is not empty
                timeL = "".join(str(trains[0]).split("(")[1].split("~")[0].split(":"))
                if int(timeL) >= int(self.reserveInfo["maxDepTime"]):
                    trains = []
        except NoResultsError:
            trains = []
        return trains

    def _try_reserve(self, train):
        try:
            return self.korail_client.reserve(train, option=self.reserveInfo["special"])
        except SoldOutError:
            print("예약을 놓쳤습니다. 다음 열차를 찾습니다.")
            return None
