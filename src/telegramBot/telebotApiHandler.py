from korail2 import ReserveOption, TrainType
from fastapi import APIRouter, Query
from pydantic import BaseModel
from datetime import datetime
from .korailReserve import Korail
from .messages import MESSAGES_INFO, MESSAGES_ERROR
import requests
import os
import subprocess
import signal
import json

router = APIRouter()


class Chat(BaseModel):
    id: int


class Message(BaseModel):
    text: str
    chat: Chat


class TelegramRequest(BaseModel):
    message: Message


def is_affirmative(data):
    return str(data).upper() == "Y" or str(data) == "예"


def is_negative(data):
    return str(data).upper() == "N" or str(data) == "아니오"


class TelebotApiHandler:
    def __init__(self):
        self.s = requests.session()
        self.BOTTOKEN = os.environ.get("BOTTOKEN")
        self.sendUrl = f"https://api.telegram.org/bot{self.BOTTOKEN}"
        self.lastSentMessage = None

    # userDict : Use like DB.
    # {
    #   123123: {
    #     "inProgress": True,
    #     "lastAction": "",
    #     "userInfo": { "korailId": "010-1111-1111", "korailPw": "123123" },
    #     "trainInfo": {"srcLocate":"광명", "dstLocate": "광주송정", "depDate": "20210204"}
    #     "pid": 9999999
    #   }
    # }
    userDict = {}

    # runningStatus : Use like DB.
    # {
    # 123123: {
    #     "pid": 9999999,
    # }
    # }
    runningStatus = {}

    # Group for get notification
    subscribes = []

    def handle_progress(self, chatId, action, data=""):
        actions = {
            0: self._reset_user,
            1: self.start_accept,
            2: self.input_id,
            3: self.input_pw,
            4: self.input_date,
            5: self.input_src_station,
            6: self.input_dst_station,
            7: self.input_dep_time,
            8: self.input_max_dep_time,
            9: self.input_train_type,
            10: self.input_special,
            11: self.start_reserve,
        }

        if action == 0:
            self._reset_user(chatId)
            return

        if len(self.runningStatus) > 0 and chatId not in self.runningStatus:
            self.sendMessage(
                chatId, "현재 다른 유저가 이용중입니다. 관리자에게 문의하세요."
            )
            return

        handler = actions.get(action, self._handle_invalid_action)
        handler(chatId, data)

    def _reset_user(self, chatId):
        if chatId in self.userDict:
            self.userDict[chatId]["inProgress"] = False
            self.userDict[chatId]["lastAction"] = 0
            self.userDict[chatId]["trainInfo"] = {}
            self.userDict[chatId]["pid"] = 9999999
        else:
            self.userDict[chatId] = {
                "inProgress": False,
                "lastAction": 0,
                "userInfo": {
                    "korailId": "no-login-yet",
                    "korailPw": "no-login-yet",
                },
                "trainInfo": {},
                "pid": 9999999,
            }

    def _handle_invalid_action(self, chatId, data):
        self.sendMessage(
            chatId,
            "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요.",
        )

    def getUserProgress(self, chatId):
        if chatId in self.userDict:
            progressNum = self.userDict[chatId]["lastAction"]
        else:
            self.handle_progress(chatId, 0)
            progressNum = 0
        inProgress = self.userDict[chatId]["inProgress"]
        return inProgress, progressNum

    def sendMessage(self, chatId, msg):
        sendUrl = f"{self.sendUrl}/sendMessage"
        params = {"chat_id": chatId, "text": msg}
        self.s.get(sendUrl, params=params)
        self.lastSentMessage = msg
        print(f"Send message to {chatId} : {msg}")
        return msg

    def start_func(self, chatId):
        msg = MESSAGES_INFO["START_MESSAGE"]
        self.userDict[chatId]["inProgress"] = True
        self.userDict[chatId]["lastAction"] = 1
        self.sendMessage(chatId, msg)
        return None

    def start_accept(self, chatId, data="Y"):
        if is_affirmative(data):
            self.userDict[chatId]["lastAction"] = 2
            msg = MESSAGES_INFO["START_ACCEPT_MESSAGE"]
        elif str(data) == os.environ.get("ADMINPW"):
            username = os.environ.get("USERID")
            password = os.environ.get("USERPW")
            if username and password:
                self.userDict[chatId]["userInfo"]["korailId"] = username
                self.userDict[chatId]["userInfo"]["korailPw"] = password
                korail = Korail()
                loginSuc = korail.login(username, password)
                print(loginSuc)
                if loginSuc:
                    msg = MESSAGES_INFO["LOGIN_SUCCESS_PROMPT"]
                    self.userDict[chatId]["lastAction"] = 4
                else:
                    self.handle_progress(chatId, 0)
                    msg = "관리자 계정으로 로그인에 문제가 발생하였습니다."
            else:
                self.handle_progress(chatId, 0)
                msg = "컨테이너에 환경변수가 초기화되지 않았습니다."
        else:
            self.handle_progress(chatId, 0)
            msg = MESSAGES_ERROR["RESERVE_INIT_CANCELLED"]
        self.sendMessage(chatId, msg)
        return None

    # 아이디 입력 함수
    def input_id(self, chatId, data):
        allowList = os.environ.get("ALLOW_LIST", "").split(",")
        if "-" not in data:
            msg = "'-'를 포함한 전화번호를 입력해주세요. 다시 입력 바랍니다."
        elif data not in allowList:
            msgToSubscribers = f"{data}는 등록되지 않은 사용자입니다."
            self.sendToSubscribers(msgToSubscribers)
            self.handle_progress(chatId, 0)
        else:
            self.userDict[chatId]["userInfo"]["korailId"] = data
            self.userDict[chatId]["lastAction"] = 3
            msg = MESSAGES_INFO["INPUT_ID_SUCCESS"]
        self.sendMessage(chatId, msg)
        return None

    # 패스워드 입력 함수
    def input_pw(self, chatId, data):
        self.userDict[chatId]["userInfo"]["korailPw"] = data
        print(self.userDict[chatId]["userInfo"])
        username = self.userDict[chatId]["userInfo"]["korailId"]
        password = self.userDict[chatId]["userInfo"]["korailPw"]
        korail = Korail()
        loginSuc = korail.login(username, password)
        print(loginSuc)
        if loginSuc:
            msg = MESSAGES_INFO["LOGIN_SUCCESS_PROMPT"]
            self.userDict[chatId]["lastAction"] = 4
            self.sendMessage(chatId, msg)
        else:
            if is_affirmative(data):
                self.start_accept(chatId)
            elif is_negative(data):
                self.handle_progress(chatId, 0)
                msg = MESSAGES_ERROR["RESERVE_FINISHED"]
                self.sendMessage(chatId, msg)
            else:
                msg = MESSAGES_ERROR["INPUT_PW_FAILURE"].format(username)
                self.sendMessage(chatId, msg)

        return None

    # 출발일 입력 함수
    def input_date(self, chatId, data):
        today = datetime.today().strftime("%Y%m%d")
        if str(data).isdigit() and len(str(data)) == 8 and data >= today:
            self.userDict[chatId]["trainInfo"]["depDate"] = data
            self.userDict[chatId]["lastAction"] = 5
            msg = MESSAGES_INFO["INPUT_DATE_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DATE_FAILURE"]
        self.sendMessage(chatId, msg)
        return None

    def input_src_station(self, chatId, data):
        self.userDict[chatId]["trainInfo"]["srcLocate"] = data
        self.userDict[chatId]["lastAction"] = 6
        msg = MESSAGES_INFO["INPUT_SRC_STATION_SUCCESS"]
        self.sendMessage(chatId, msg)
        return None

    def input_dst_station(self, chatId, data):

        self.userDict[chatId]["trainInfo"]["dstLocate"] = data
        self.userDict[chatId]["lastAction"] = 7
        msg = MESSAGES_INFO["INPUT_DST_STATION_SUCCESS"]

        self.sendMessage(chatId, msg)
        return None

    def input_dep_time(self, chatId, data):
        if len(str(data)) == 4 and str(data).isdecimal():
            self.userDict[chatId]["trainInfo"]["depTime"] = data
            self.userDict[chatId]["lastAction"] = 8
            msg = MESSAGES_INFO["INPUT_DEP_TIME_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DEP_TIME_FAILURE"]

        self.sendMessage(chatId, msg)
        return None

    def input_max_dep_time(self, chatId, data):
        if len(str(data)) == 4 and str(data).isdecimal():
            self.userDict[chatId]["trainInfo"]["maxDepTime"] = data
            self.userDict[chatId]["lastAction"] = 9
            msg = MESSAGES_INFO["INPUT_MAX_DEP_TIME_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DEP_TIME_FAILURE"]

        self.sendMessage(chatId, msg)
        return None

    def input_train_type(self, chatId, data):
        if str(data) in ["1", "2"]:
            if str(data) == "1":
                trainType = TrainType.KTX
                trainTypeShow = "KTX"
            elif str(data) == "2":
                trainType = TrainType.ALL
                trainTypeShow = "ALL"
            self.userDict[chatId]["trainInfo"]["trainType"] = trainType
            self.userDict[chatId]["trainInfo"]["trainTypeShow"] = trainTypeShow
            self.userDict[chatId]["lastAction"] = 10
            msg = MESSAGES_INFO["INPUT_TRAIN_TYPE_SUCCESS"]
        else:
            msg = """입력하신 값이 1,2 중 하나가 아닙니다. 다시 입력해주세요."""
        self.sendMessage(chatId, msg)
        return None

    def input_special(self, chatId, data):
        special_options = {
            "1": ReserveOption.GENERAL_FIRST,
            "2": ReserveOption.GENERAL_ONLY,
            "3": ReserveOption.SPECIAL_FIRST,
            "4": ReserveOption.SPECIAL_ONLY,
        }

        if data in special_options:
            specialInfo = special_options[data]
            self.userDict[chatId]["trainInfo"]["specialInfo"] = specialInfo
            self.userDict[chatId]["trainInfo"]["specialInfoShow"] = specialInfo
            self.userDict[chatId]["lastAction"] = 11

            train_info = self.userDict[chatId]["trainInfo"]
            msg = MESSAGES_INFO["INPUT_SPECIAL_SUCCESS"].format(
                depDate=train_info["depDate"],
                srcLocate=train_info["srcLocate"],
                dstLocate=train_info["dstLocate"],
                depTime=train_info["depTime"],
                maxDepTime=train_info["maxDepTime"],
                trainTypeShow=train_info["trainTypeShow"],
                specialInfoShow=specialInfo,
            )
        else:
            msg = "입력하신 값이 1,2,3,4 중 하나가 아닙니다. 다시 입력해주세요."

        self.sendMessage(chatId, msg)
        return None

    def start_reserve(self, chatId, data):
        try:
            if is_affirmative(data):
                self.userDict[chatId]["lastAction"] = 12
                train_info = self.userDict[chatId]["trainInfo"]
                user_info = self.userDict[chatId]["userInfo"]

                arguments = [
                    user_info["korailId"],
                    user_info["korailPw"],
                    train_info["depDate"],
                    train_info["srcLocate"],
                    train_info["dstLocate"],
                    f"{train_info['depTime']}00",
                    train_info["trainType"],
                    train_info["specialInfo"],
                    chatId,
                    train_info["maxDepTime"],
                ]
                arguments = [str(argument) for argument in arguments]
                print(f"Starting reservation, arguments: {arguments}")
                
                cmd = ["python", "-m", "src.telegramBot.telebotBackProcess"] + arguments
                proc = subprocess.Popen(cmd)
                self.userDict[chatId]["pid"] = proc.pid
                self.runningStatus[chatId] = {
                    "pid": proc.pid,
                    "korailId": user_info["korailId"],
                }

                # msgToSubscribers = f"{user_info['korailId']}의 {train_info['srcLocate']}에서 {train_info['dstLocate']}로 {train_info['depDate']}에 출발하는 열차 예약이 시작되었습니다."
                # self.sendToSubscribers(msgToSubscribers)

                msg = MESSAGES_INFO["RESERVE_STARTED"]
            elif is_negative(data):
                self.handle_progress(chatId, 0)
                msg = MESSAGES_ERROR["RESERVE_CANCELLED"]
            else:
                msg = MESSAGES_ERROR["INPUT_WRONG"]
            self.sendMessage(chatId, msg)
        except Exception as e:
            self.sendMessage(chatId, f"예약 시작 중 오류가 발생했습니다. /start를 입력해 다시 시작해 주세요")
            print(f"Error starting reservation, {chatId}: {str(e)}")

    def already_doing(self, chatId):
        train_info = self.userDict[chatId]["trainInfo"]
        msg = MESSAGES_ERROR["RESERVE_ALREADY_DOING"].format(
            depDate=train_info["depDate"],
            srcLocate=train_info["srcLocate"],
            dstLocate=train_info["dstLocate"],
            depTime=train_info["depTime"],
            trainTypeShow=train_info["trainTypeShow"],
            specialInfoShow=train_info["specialInfoShow"],
        )
        self.sendMessage(chatId, msg)

    def cancel_func(self, chatId):
        userPid = self.userDict[chatId]["pid"]
        if userPid != 9999999:
            os.kill(userPid, signal.SIGTERM)
            print(f"실행중인 프로세스 {userPid}를 종료합니다.")

            del self.runningStatus[chatId]
            msgToSubscribers = f'{self.userDict[chatId]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
            self.sendToSubscribers(msgToSubscribers)

        self.handle_progress(chatId, 0)
        msg = MESSAGES_ERROR["RESERVE_FINISHED"]
        self.sendMessage(chatId, msg)

        return None

    def subscribe_user(self, chatId):
        if chatId not in self.subscribes:
            self.subscribes.append(chatId)
            data = "열차 이용정보 구독 설정이 완료되었습니다."
        else:
            data = "이미 구독했습니다."
        self.sendMessage(chatId, data)

    def sendToSubscribers(self, data):
        for chatId in self.subscribes:
            self.sendMessage(chatId, data)

    def get_status_info(self, chatId):
        count = len(self.runningStatus)
        usersKorailIds = [
            state["korailId"] for state in dict.values(self.runningStatus)
        ]
        data = f"총 {count}개의 예약이 실행중입니다. 이용중인 사용자 : {usersKorailIds}"
        self.sendMessage(chatId, data)

    def cancel_all(self, chatId):
        count = len(self.runningStatus)
        pids = [state["pid"] for state in dict.values(self.runningStatus)]
        usersKorailIds = [
            state["korailId"] for state in dict.values(self.runningStatus)
        ]
        usersChatId = dict.keys(self.runningStatus)

        for pid in pids:
            os.kill(pid, signal.SIGTERM)
            print(f"프로세스 {pid}가 종료되었습니다.")

        dataForManager = f"총 {count}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {usersKorailIds}"
        self.sendMessage(chatId, dataForManager)

        dataForUser = MESSAGES_ERROR["RESERVE_CANCELLED_BY_ADMIN"]
        for user in usersChatId:
            self.sendMessage(user, dataForUser)
            self.handle_progress(user, 0)

        self.runningStatus = {}

    def get_all_users(self, chatId):
        allUsers = [user["userInfo"]["korailId"] for user in dict.values(self.userDict)]
        data = f"총 {len(allUsers)}명의 유저가 있습니다 : {allUsers}"
        self.sendMessage(chatId, data)

    def return_help(self, chatId):
        msg = MESSAGES_INFO["HELP_MESSAGE"]
        self.sendMessage(chatId, msg)


telebot_handler = TelebotApiHandler()


@router.post("/telebot/message")
async def handle_chat_message(request: TelegramRequest):
    data = request.model_dump()
    print("Request:", json.dumps(data, sort_keys=True, indent=4))

    if "edited_message" in data:
        return "Edited message"
    if "my_chat_member" in data:
        return "Chat member"

    try:
        messageText = data["message"]["text"].strip()
        chatId = int(data["message"]["chat"]["id"])
    except KeyError:
        msg = "코레일 예약봇입니다.\n시작하시려면 /start 를 입력해주세요."
        chatId = int(data["message"]["chat"]["id"])
        telebot_handler.sendMessage(chatId, msg)
        return {"msg": msg}

    inProgress, progressNum = telebot_handler.getUserProgress(chatId)
    print(
        f"CHATID : {chatId} , TEXT : {messageText}, InProgress : {inProgress}, Progress : {progressNum}"
    )

    command_handlers = {
        "/cancel": telebot_handler.cancel_func,
        "/subscribe": telebot_handler.subscribe_user,
        "/status": telebot_handler.get_status_info,
        "/cancelall": telebot_handler.cancel_all,
        "/allusers": telebot_handler.get_all_users,
        "/help": telebot_handler.return_help,
        "/start": telebot_handler.start_func,
    }

    if messageText in command_handlers:
        command_handlers[messageText](chatId)
    elif messageText.split(" ")[0] == "/broadcast":
        telebot_handler.broadcast_message(messageText)
    elif progressNum == 12:
        telebot_handler.already_doing(chatId)
    elif messageText[0] == "/":
        telebot_handler.sendMessage(chatId, "잘못된 명령어 입니다.")
    else:
        if inProgress:
            telebot_handler.handle_progress(chatId, progressNum, messageText)
        else:
            telebot_handler.sendMessage(
                chatId,
                "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요.\n",
            )

    return {"msg": telebot_handler.lastSentMessage}


@router.post("/telebot/reservations/{chatId}/completion")
def send_reservation_status(
    chatId: int, 
    msg: str = Query(...), 
    status: str = Query(...)
):
    """예약 프로세스에서 결과를 받아 사용자에게 메세지 전송

    Args:
        chatId (int): 텔레그램 채팅방 ID
        msg (str): 전송할 메시지
        status (str): 예약 상태 코드 ("0"이면 예약 완료)

    """
    if status == "0":
        print("예약 완료, 상태 초기화")
        telebot_handler.handle_progress(chatId, 0)
    telebot_handler.sendMessage(chatId, msg)

    del telebot_handler.runningStatus[chatId]
    # msgToSubscribers = f'{telebot_handler.userDict[chatId]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
    # telebot_handler.sendToSubscribers(msgToSubscribers)
