import requests
import time
import sys
from korail2 import Korail as KorailBase
from korail2 import ReserveOption, TrainType, SoldOutError, NoResultsError
from .messages import MESSAGES_INFO, MESSAGES_ERROR

sys.setrecursionlimit(10**7)


class Korail:
    def __init__(self):
        self.korailObj = None
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
        self.korailObj = KorailBase(username, password, auto_login=False)
        self.loginSuc = self.korailObj.login()
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
        while not reserveOne:
            trains = self._search_trains()
            for train in trains:
                print(f"열차 발견 : {train} <- 에 대한 예약을 시작합니다.")
                reserveOne = self._try_reserve(train)
                if reserveOne:
                    self.reserveInfo["reserveSuc"] = True
                    break
            time.sleep(self.interval)
        return reserveOne

    def _search_trains(self):
        try:
            trains = self.korailObj.search_train(
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
            return self.korailObj.reserve(train, option=self.reserveInfo["special"])
        except SoldOutError:
            print("예약을 놓쳤습니다. 다음 열차를 찾습니다.")
            return None

    def sendReservationStatus(self, reserveInfo):
        print(reserveInfo)
        result = self.reserveInfo["reserveSuc"]
        chatId = self.chatId

        if result == "wrong":
            msg = MESSAGES_ERROR["RESERVE_WRONG"]
        elif result:
            msg = MESSAGES_INFO["RESERVE_SUCCESS"].format(reserveInfo=reserveInfo)
        else:
            msg = MESSAGES_ERROR["RESERVE_FAILED"]
        self.sendBotStateChange(chatId, msg, 0)
        return None

    def sendBotStateChange(self, chatId, msg, status):
        # callbackUrl = f"https://127.0.0.1:8080/telebot/{chatId}/completion" # if using ssl inside docker, use this
        callbackUrl = f"http://127.0.0.1:8080/telebot/reservations/{chatId}/completion"
        print(chatId, msg, status)
        param = {"chatId": chatId, "msg": msg, "status": status}
        s = requests.session()
        s.get(callbackUrl, params=param, verify=False)
        return None
