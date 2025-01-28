from korail2 import ReserveOption, TrainType
from datetime import datetime
from .korailReserve import ReserveHandler
from .messages import MESSAGES_INFO, MESSAGES_ERROR

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

import requests
import os
import subprocess
import signal
import json


def is_affirmative(data):
    return str(data).upper() == "Y" or str(data) == "예"


def is_negative(data):
    return str(data).upper() == "N" or str(data) == "아니오"


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.app = ApplicationBuilder().token(self.token).build()
        self._register_handlers()
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

    def set_webhook(self, url):
        self.app.set_webhook(url)
        print(f"Webhook set to {url}")

    def delete_webhook(self):
        self.app.delete_webhook()
        print("Webhook deleted")

    def _register_handlers(self):
        """Register all bot command handlers"""

        # 명령어 처리를 위한 핸들러
        command_handlers = {
            "cancel": self.cancel_func,
            "subscribe": self.subscribe_user,
            "status": self.get_status_info,
            "cancelall": self.cancel_all,
            "allusers": self.get_all_users,
            "help": self.return_help,
            "start": self.start_func,
            "broadcast": self.broadcast_message,
        }
        for command, handler in command_handlers.items():
            self.app.add_handler(CommandHandler(command, handler))

        # 일반 메세지 처리를 위한 핸들러
        self.app.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), self._handle_chat_message)
        )
        self.app.add_handler(
            MessageHandler(filters.COMMAND, self._handle_unknown_command)
        )

    async def handle_progress(self, chatId, action, data=""):
        actions = {
            1: self._start_accept,
            2: self._input_id,
            3: self._input_pw,
            4: self._input_date,
            5: self._input_src_station,
            6: self._input_dst_station,
            7: self._input_dep_time,
            8: self._input_max_dep_time,
            9: self._input_train_type,
            10: self._input_special,
            11: self._start_reserve,
        }

        if len(self.runningStatus) > 0 and chatId not in self.runningStatus:
            await self.send_message(
                chatId, "현재 다른 유저가 이용중입니다. 관리자에게 문의하세요."
            )
            return

        handler = actions.get(action, self._handle_invalid_action)
        await handler(chatId, data)

    async def _handle_chat_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        messageText = update.message.text
        chatId = update.effective_chat.id
        inProgress, progressNum = self._get_user_progress(chatId)
        print(
            f"CHATID : {chatId} , TEXT : {messageText}, InProgress : {inProgress}, Progress : {progressNum}"
        )
        if progressNum == 12:
            await self._already_doing(chatId)
        else:
            if inProgress:
                await self.handle_progress(chatId, progressNum, messageText)
            else:
                await self.send_message(
                    chatId,
                    "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요.\n",
                )

        return {"msg": self.lastSentMessage}

    async def _handle_unknown_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        chatId = update.message.chat_id
        await self.send_message(
            chatId, "알 수 없는 명령어입니다. /help를 입력해 도움말을 확인하세요."
        )

    def _reset_user_state(self, chatId):
        self.userDict[chatId]["inProgress"] = False
        self.userDict[chatId]["lastAction"] = 0
        self.userDict[chatId]["trainInfo"] = {}
        self.userDict[chatId]["pid"] = 9999999

    def _create_user(self, chatId):
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

    def ensure_user_exists(self, chat_id):
        """Ensure user exists in userDict"""
        if chat_id not in self.userDict:
            self._create_user(chat_id)

    async def _handle_invalid_action(self, chatId, data):
        await self.send_message(
            chatId,
            "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요.",
        )

    def _get_user_progress(self, chatId):
        if chatId in self.userDict:
            progressNum = self.userDict[chatId]["lastAction"]
        else:
            self._create_user(chatId)
            progressNum = 0
        inProgress = self.userDict[chatId]["inProgress"]
        return inProgress, progressNum

    async def send_message(self, chatId, msg):
        """Send message using telegram bot API"""
        try:
            message = await self.app.bot.send_message(chat_id=chatId, text=msg)
            self.lastSentMessage = msg
            print(f"Send message to {chatId} : {msg}")
            return message
        except TelegramError as e:
            print(f"Failed to send message to {chatId}: {e}")
            return None

    async def start_func(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        msg = MESSAGES_INFO["START_MESSAGE"]
        self.userDict[chatId]["inProgress"] = True
        self.userDict[chatId]["lastAction"] = 1
        await self.send_message(chatId, msg)
        return None

    async def _start_accept(self, chatId, data="Y"):
        msg = "잘못된 입력입니다. 다시 시도해주세요."  # default message

        if is_affirmative(data):
            self.userDict[chatId]["lastAction"] = 2
            msg = MESSAGES_INFO["START_ACCEPT_MESSAGE"]

        elif data == os.environ.get("ADMINPW"):
            username = os.environ.get("USERID")
            password = os.environ.get("USERPW")

            if not (username and password):
                self._reset_user_state(chatId)
                msg = "컨테이너에 환경변수가 초기화되지 않았습니다."
                await self.send_message(chatId, msg)
                return None

            self.userDict[chatId]["userInfo"].update(
                {"korailId": username, "korailPw": password}
            )

            reserve_handler = ReserveHandler()
            if reserve_handler.login(username, password):
                msg = MESSAGES_INFO["LOGIN_SUCCESS_PROMPT"]
                self.userDict[chatId]["lastAction"] = 4
            else:
                self._reset_user_state(chatId)
                msg = "관리자 계정으로 로그인에 문제가 발생하였습니다."

        elif is_negative(data):
            self._reset_user_state(chatId)
            msg = MESSAGES_ERROR["RESERVE_INIT_CANCELLED"]

        await self.send_message(chatId, msg)
        return None

    # 아이디 입력 함수
    async def _input_id(self, chatId, data):
        allowList = os.environ.get("ALLOW_LIST", "").split(",")
        if "-" not in data:
            msg = "'-'를 포함한 전화번호를 입력해주세요. 다시 입력 바랍니다."
        elif data not in allowList:
            msgToSubscribers = f"{data}는 등록되지 않은 사용자입니다."
            await self.broadcast_message(msgToSubscribers)
            self._reset_user_state(chatId)
        else:
            self.userDict[chatId]["userInfo"]["korailId"] = data
            self.userDict[chatId]["lastAction"] = 3
            msg = MESSAGES_INFO["INPUT_ID_SUCCESS"]
        await self.send_message(chatId, msg)
        return None

    # 패스워드 입력 함수
    async def _input_pw(self, chatId, data):
        self.userDict[chatId]["userInfo"]["korailPw"] = data
        print(self.userDict[chatId]["userInfo"])
        username = self.userDict[chatId]["userInfo"]["korailId"]
        password = self.userDict[chatId]["userInfo"]["korailPw"]
        reserve_handler = ReserveHandler()
        loginSuc = reserve_handler.login(username, password)
        print(loginSuc)
        if loginSuc:
            msg = MESSAGES_INFO["LOGIN_SUCCESS_PROMPT"]
            self.userDict[chatId]["lastAction"] = 4
            await self.send_message(chatId, msg)
        else:
            if is_affirmative(data):
                await self._start_accept(chatId)
            elif is_negative(data):
                self._reset_user_state(chatId)
                msg = MESSAGES_INFO["RESERVE_FINISHED"]
                await self.send_message(chatId, msg)
            else:
                msg = MESSAGES_ERROR["INPUT_PW_FAILURE"].format(username)
                await self.send_message(chatId, msg)

        return None

    # 출발일 입력 함수
    async def _input_date(self, chatId, data):
        today = datetime.today().strftime("%Y%m%d")
        if str(data).isdigit() and len(str(data)) == 8 and data >= today:
            self.userDict[chatId]["trainInfo"]["depDate"] = data
            self.userDict[chatId]["lastAction"] = 5
            msg = MESSAGES_INFO["INPUT_DATE_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DATE_FAILURE"]
        await self.send_message(chatId, msg)
        return None

    async def _input_src_station(self, chatId, data):
        self.userDict[chatId]["trainInfo"]["srcLocate"] = data
        self.userDict[chatId]["lastAction"] = 6
        msg = MESSAGES_INFO["INPUT_SRC_STATION_SUCCESS"]
        await self.send_message(chatId, msg)
        return None

    async def _input_dst_station(self, chatId, data):
        self.userDict[chatId]["trainInfo"]["dstLocate"] = data
        self.userDict[chatId]["lastAction"] = 7
        msg = MESSAGES_INFO["INPUT_DST_STATION_SUCCESS"]

        await self.send_message(chatId, msg)
        return None

    async def _input_dep_time(self, chatId, data):
        if len(str(data)) == 4 and str(data).isdecimal():
            self.userDict[chatId]["trainInfo"]["depTime"] = data
            self.userDict[chatId]["lastAction"] = 8
            msg = MESSAGES_INFO["INPUT_DEP_TIME_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DEP_TIME_FAILURE"]

        await self.send_message(chatId, msg)
        return None

    async def _input_max_dep_time(self, chatId, data):
        if len(str(data)) == 4 and str(data).isdecimal():
            self.userDict[chatId]["trainInfo"]["maxDepTime"] = data
            self.userDict[chatId]["lastAction"] = 9
            msg = MESSAGES_INFO["INPUT_MAX_DEP_TIME_SUCCESS"]
        else:
            msg = MESSAGES_ERROR["INPUT_DEP_TIME_FAILURE"]

        await self.send_message(chatId, msg)
        return None

    async def _input_train_type(self, chatId, data):
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
        await self.send_message(chatId, msg)
        return None

    async def _input_special(self, chatId, data):
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

        await self.send_message(chatId, msg)
        return None

    async def _start_reserve(self, chatId, data):
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

                pid = self._start_background_process(arguments)

                self.userDict[chatId]["pid"] = pid
                self.runningStatus[chatId] = {
                    "pid": pid,
                    "korailId": user_info["korailId"],
                }

                # msgToSubscribers = f"{user_info['korailId']}의 {train_info['srcLocate']}에서 {train_info['dstLocate']}로 {train_info['depDate']}에 출발하는 열차 예약이 시작되었습니다."
                # self.sendToSubscribers(msgToSubscribers)

                msg = MESSAGES_INFO["RESERVE_STARTED"]
            elif is_negative(data):
                self._reset_user_state(chatId)
                msg = MESSAGES_ERROR["RESERVE_CANCELLED"]
            else:
                msg = MESSAGES_ERROR["INPUT_WRONG"]
            await self.send_message(chatId, msg)
        except Exception as e:
            await self.send_message(
                chatId,
                "예약 시작 중 오류가 발생했습니다. /start를 입력해 다시 시작해 주세요",
            )
            print(f"Error starting reservation, {chatId}: {str(e)}")

    def _start_background_process(self, arguments):
        try:
            cmd = ["python", "-m", "telegramBot.telebotBackProcess"] + arguments
            cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd
            )

            return process.pid

        except Exception as e:
            print(f"Failed to start process: {str(e)}")
            return False, str(e)

    async def _already_doing(self, chatId):
        train_info = self.userDict[chatId]["trainInfo"]
        msg = MESSAGES_ERROR["RESERVE_ALREADY_DOING"].format(
            depDate=train_info["depDate"],
            srcLocate=train_info["srcLocate"],
            dstLocate=train_info["dstLocate"],
            depTime=train_info["depTime"],
            trainTypeShow=train_info["trainTypeShow"],
            specialInfoShow=train_info["specialInfoShow"],
        )
        await self.send_message(chatId, msg)

    async def cancel_func(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        userPid = self.userDict[chatId]["pid"]

        if chatId not in self.runningStatus:
            msg = "진행중인 예약이 없습니다."
            await self.send_message(chatId, msg)

        elif userPid != 9999999:
            os.kill(userPid, signal.SIGTERM)
            print(f"실행중인 프로세스 {userPid}를 종료합니다.")

            del self.runningStatus[chatId]
            msgToSubscribers = f'{self.userDict[chatId]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
            await self.broadcast_message(msgToSubscribers)

            self._reset_user_state(chatId)
            msg = MESSAGES_INFO["RESERVE_FINISHED"]
            await self.send_message(chatId, msg)

        return None

    async def subscribe_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        if chatId not in self.subscribes:
            self.subscribes.append(chatId)
            data = "열차 이용정보 구독 설정이 완료되었습니다."
        else:
            data = "이미 구독했습니다."
        await self.send_message(chatId, data)

    async def broadcast_message(self, data):
        """Send message to all subscribers"""
        for chatId in self.subscribes:
            await self.send_message(chatId, data)

    async def get_status_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        count = len(self.runningStatus)
        usersKorailIds = [
            state["korailId"] for state in dict.values(self.runningStatus)
        ]
        data = f"총 {count}개의 예약이 실행중입니다. 이용중인 사용자 : {usersKorailIds}"
        await self.send_message(chatId, data)

    async def cancel_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
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
        await self.send_message(chatId, dataForManager)

        dataForUser = MESSAGES_ERROR["RESERVE_CANCELLED_BY_ADMIN"]
        for user in usersChatId:
            await self.send_message(user, dataForUser)
            self.handle_progress(user, 0)

        self.runningStatus = {}

    async def get_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        allUsers = [user["userInfo"]["korailId"] for user in dict.values(self.userDict)]
        data = f"총 {len(allUsers)}명의 유저가 있습니다 : {allUsers}"
        await self.send_message(chatId, data)

    async def return_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chatId = update.message.chat_id
        self.ensure_user_exists(chatId)
        msg = MESSAGES_INFO["HELP_MESSAGE"]
        await self.send_message(chatId, msg)
