import os
import requests
import time
import sys
from korail2 import Korail
from korail2 import ReserveOption, TrainType, SoldOutError, NoResultsError
from .messages import Messages

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
        self.korail_client = Korail(username, password, auto_login=False)
        self.loginSuc = self.korail_client.login()
        return self.loginSuc

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

            except Exception as e:
                error_count += 1
                last_error_time = time.time()
                print(f"예약 시도 중 오류 발생: {str(e)}")

                # 연속 에러가 10회 이상 발생하면 세션 재로그인
                if error_count >= 10:
                    print("연속 에러 발생으로 세션 재로그인 시도")
                    try:
                        self.login(
                            self.korail_client.username, self.korail_client.password
                        )
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
            trains = self.korail_client.search_train(
                self.reserveInfo["srcLocate"],
                self.reserveInfo["dstLocate"],
                self.reserveInfo["depDate"],
                self.reserveInfo["depTime"],
                train_type=self.reserveInfo["trainType"],
            )
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
