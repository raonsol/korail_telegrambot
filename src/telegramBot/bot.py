import os
import subprocess
import signal
from datetime import datetime, time

from korail2 import ReserveOption, TrainType
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from telegram.error import TelegramError

from .korail_client import ReserveHandler
from .messages import Messages
from .calendar_keyboard import create_calendar, handle_calendar_action


def is_affirmative(data):
    return str(data).upper() == "Y" or str(data) == "예"


def is_negative(data):
    return str(data).upper() == "N" or str(data) == "아니오"


def is_valid_time(str):
    try:
        print("time: ", str)
        if not (len(str) == 4 and str.isdigit()):
            return False

        hours = int(str[:2])
        minutes = int(str[2:])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return False
        else:
            return True

    except ValueError:
        return False


def is_today(date: str):
    date_alt = datetime.strptime(date, "%Y%m%d")
    today = datetime.today().strftime("%Y%m%d")
    return date_alt == today


def is_past_time(time: str):
    time_alt = datetime.strptime(time, "%H%M")
    current_time = datetime.now().time()
    return time_alt < current_time


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

    async def set_webhook(self, url):
        await self.app.bot.set_webhook(url)
        print(f"Webhook set to {url}")

    async def delete_webhook(self):
        await self.app.bot.delete_webhook()
        print("Webhook deleted")
    
    def start(self):
        self.app.start()
    
    def stop(self):
        self.app.stop()

    def _register_handlers(self):
        """Register all bot command handlers"""

        # 명령어 처리를 위한 핸들러
        command_handlers = {
            "start": self.start_func,
            "cancel": self.cancel_func,
            "subscribe": self.subscribe_user,
            "status": self.get_status_info,
            "cancelall": self.cancel_all,
            "allusers": self.get_all_users,
            "help": self.return_help,
            "broadcast": self.broadcast_message,
        }
        for command, handler in command_handlers.items():
            self.app.add_handler(CommandHandler(command, handler))

        # 알 수 없는 명령어 처리를 위한 핸들러
        self.app.add_handler(
            MessageHandler(filters.COMMAND, self._handle_unknown_command)
        )
        # 일반 메세지 처리를 위한 핸들러
        self.app.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), self._handle_chat_message)
        )
        # 메뉴 버튼 처리를 위한 핸들러
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

    async def handle_progress(self, chat_id, action, data=""):
        actions = {
            1: self._start_accept,
            2: self._input_id,
            3: self._input_pw,
            4: self._input_date_str,
            5: self._input_src_station,
            6: self._input_dst_station,
            7: self._input_dep_time,
            8: self._input_max_dep_time,
            9: self._input_train_type,
            10: self._input_seat_type,
            11: self._start_reserve,
        }

        if len(self.runningStatus) > 0 and chat_id not in self.runningStatus:
            await self.send_message(
                chat_id, "현재 다른 유저가 이용중입니다. 관리자에게 문의하세요."
            )
            return

        handler = actions.get(action, self._handle_invalid_action)
        await handler(chat_id, data)

    async def _handle_unknown_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        chat_id = update.message.chat_id
        await self.send_message(
            chat_id, "알 수 없는 명령어입니다. /help를 입력해 도움말을 확인하세요."
        )

    async def _handle_chat_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        messageText = update.message.text
        chat_id = update.effective_chat.id
        inProgress, progressNum = self._get_user_progress(chat_id)
        print(
            f"chat_id : {chat_id} , TEXT : {messageText}, InProgress : {inProgress}, Progress : {progressNum}"
        )
        if progressNum == 12:
            await self._already_doing(chat_id)
        else:
            if inProgress:
                await self.handle_progress(chat_id, progressNum, messageText)
            else:
                await self.send_message(
                    chat_id,
                    "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요.\n",
                )

        return {"msg": self.lastSentMessage}

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id

        if query.data.startswith("start_"):
            await self._start_accept(chat_id, query.data)
        elif query.data.startswith("train_type_"):
            await self._input_train_type(chat_id, query.data)
        elif query.data.startswith("seat_type_"):
            await self._input_seat_type(chat_id, query.data)
        elif query.data.startswith("confirm_"):
            await self._start_reserve(chat_id, query.data)
        elif query.data.startswith("calendar_"):
            selected, date = await handle_calendar_action(update, context)
            if selected:
                await self._input_date(chat_id, date)

    def _reset_user_state(self, chat_id):
        self.userDict[chat_id]["inProgress"] = False
        self.userDict[chat_id]["lastAction"] = 0
        self.userDict[chat_id]["trainInfo"] = {}
        self.userDict[chat_id]["pid"] = 9999999

    def _create_user(self, chat_id):
        self.userDict[chat_id] = {
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

    async def _handle_invalid_action(self, chat_id, data):
        await self.send_message(
            chat_id,
            "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요.",
        )

    def _get_user_progress(self, chat_id):
        if chat_id in self.userDict:
            progressNum = self.userDict[chat_id]["lastAction"]
        else:
            self._create_user(chat_id)
            progressNum = 0
        inProgress = self.userDict[chat_id]["inProgress"]
        return inProgress, progressNum

    async def send_message(self, chat_id, text, reply_markup=None):
        """Send message using telegram bot API"""
        try:
            message = await self.app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            self.lastSentMessage = text
            print(f"Send message to {chat_id} : {text}")
            return message
        except TelegramError as e:
            print(f"Failed to send message to {chat_id}: {e}")
            return None

    async def start_func(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        self.userDict[chat_id]["inProgress"] = True
        self.userDict[chat_id]["lastAction"] = 1

        keyboard = [[InlineKeyboardButton("시작하기", callback_data="start_yes")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=Messages.Info.START_MESSAGE,
            reply_markup=reply_markup,
        )

        return None

    async def _start_accept(self, chat_id, data):
        if data == os.environ.get("ADMINPW"):
            username = os.environ.get("USERID")
            password = os.environ.get("USERPW")

            if not (username and password):
                self._reset_user_state(chat_id)
                msg = "관리자 계정 정보가 설정되지 않았습니다."
                await self.send_message(chat_id, msg)
                return None

            self.userDict[chat_id]["userInfo"].update(
                {"korailId": username, "korailPw": password}
            )

            reserve_handler = ReserveHandler()
            if reserve_handler.login(username, password):
                msg = Messages.Info.INPUT_DATE
                self.userDict[chat_id]["lastAction"] = 4
                await self.send_message(chat_id, msg, reply_markup=create_calendar())
            else:
                self._reset_user_state(chat_id)
                msg = "관리자 계정으로 로그인에 문제가 발생하였습니다."
                await self.send_message(chat_id, msg)
            return None

        if data == "start_yes":
            self.userDict[chat_id]["lastAction"] = 2
            msg = Messages.Info.INPUT_ID
        else:
            msg = "잘못된 입력입니다. 다시 시도해주세요."

        await self.send_message(chat_id, msg)
        return None

    async def _input_id(self, chat_id, data):
        allowList = os.environ.get("ALLOW_LIST", "").split(",")
        if "-" not in data:
            msg = "'-'를 포함한 전화번호를 입력해주세요. 다시 입력 바랍니다."
        elif data not in allowList:
            msgToSubscribers = f"{data}는 등록되지 않은 사용자입니다."
            await self.broadcast_message(msgToSubscribers)
            self._reset_user_state(chat_id)
        else:
            self.userDict[chat_id]["userInfo"]["korailId"] = data
            self.userDict[chat_id]["lastAction"] = 3
            msg = Messages.Info.INPUT_PW
        await self.send_message(chat_id, msg)
        return None

    # 패스워드 입력 함수
    async def _input_pw(self, chat_id, data):
        self.userDict[chat_id]["userInfo"]["korailPw"] = data
        print(self.userDict[chat_id]["userInfo"])
        username = self.userDict[chat_id]["userInfo"]["korailId"]
        password = self.userDict[chat_id]["userInfo"]["korailPw"]
        reserve_handler = ReserveHandler()
        loginSuc = reserve_handler.login(username, password)
        print(loginSuc)
        if loginSuc:
            msg = Messages.Info.INPUT_DATE
            self.userDict[chat_id]["lastAction"] = 4
            await self.send_message(chat_id, msg, reply_markup=create_calendar())
        else:
            if is_affirmative(data):
                await self._start_accept(chat_id, "start_yes")
            elif is_negative(data):
                self._reset_user_state(chat_id)
                msg = Messages.Info.RESERVE_FINISHED
                await self.send_message(chat_id, msg)
            else:
                msg = Messages.Error.INPUT_PW_FAILURE.format(username)
                await self.send_message(chat_id, msg)

        return None

    # 출발일 입력 함수 (직접 입력시)
    async def _input_date_str(self, chat_id, data):
        try:
            date = datetime.strptime(data, "%Y%m%d")
            await self._input_date(chat_id, date)
        except ValueError:
            msg = Messages.Error.INPUT_DATE_FAILURE
            await self.send_message(chat_id, msg, reply_markup=create_calendar())
            return None

    # 출발일 입력 함수 (캘린더 키보드 선택시)
    async def _input_date(self, chat_id, data: datetime):
        today = datetime.today().date()
        if data.date() >= today:
            self.userDict[chat_id]["trainInfo"]["depDate"] = data.strftime("%Y%m%d")
            self.userDict[chat_id]["lastAction"] = 5
            msg = Messages.Info.INPUT_SRC_STATION
            await self.send_message(chat_id, msg)
        else:
            msg = Messages.Error.INPUT_DATE_FAILURE
            await self.send_message(chat_id, msg, reply_markup=create_calendar())
        return None

    async def _input_src_station(self, chat_id, data):
        self.userDict[chat_id]["trainInfo"]["srcLocate"] = data
        self.userDict[chat_id]["lastAction"] = 6
        msg = Messages.Info.INPUT_DST_STATION
        await self.send_message(chat_id, msg)
        return None

    async def _input_dst_station(self, chat_id, data):
        self.userDict[chat_id]["trainInfo"]["dstLocate"] = data
        self.userDict[chat_id]["lastAction"] = 7
        msg = Messages.Info.INPUT_DEP_TIME

        await self.send_message(chat_id, msg)
        return None

    async def _input_dep_time(self, chat_id, data):
        dep_date = self.userDict[chat_id]["trainInfo"]["depDate"]
        if not is_valid_time(str(data)):
            msg = Messages.Error.INPUT_DEP_TIME_FAILURE
        elif is_today(dep_date) and is_past_time(str(data)):
            msg = Messages.Error.INPUT_DEP_TIME_PAST_FAILURE
        else:
            self.userDict[chat_id]["trainInfo"]["depTime"] = data
            self.userDict[chat_id]["lastAction"] = 8
            msg = Messages.Info.INPUT_MAX_DEP_TIME
        await self.send_message(chat_id, msg)
        return None

    async def _input_max_dep_time(self, chat_id, data):
        dep_time = self.userDict[chat_id]["trainInfo"]["depTime"]
        if not is_valid_time(str(data)):
            msg = Messages.Error.INPUT_DEP_TIME_FAILURE
            await self.send_message(chat_id, msg)
        elif int(data) < int(dep_time):
            msg = Messages.Error.INPUT_DEP_TIME_MAX_PAST_FAILURE
            await self.send_message(chat_id, msg)
        else:
            self.userDict[chat_id]["trainInfo"]["maxDepTime"] = data
            self.userDict[chat_id]["lastAction"] = 9
            await self._send_train_type_options(chat_id)
        return None

    async def _send_train_type_options(self, chat_id):
        """기차 옵션 선택을 위해 인라인 키보드 전송"""
        keyboard = [
            [
                InlineKeyboardButton("KTX", callback_data="train_type_1"),
                InlineKeyboardButton("모든 열차", callback_data="train_type_2"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=Messages.Info.INPUT_TRAIN_TYPE,
            reply_markup=reply_markup,
        )

    async def _input_train_type(self, chat_id, data):
        train_type_map = {
            "train_type_1": (TrainType.KTX, "KTX"),
            "train_type_2": (TrainType.ALL, "모든 열차"),
        }
        if data in train_type_map:
            trainType, trainTypeShow = train_type_map[data]
            self.userDict[chat_id]["trainInfo"]["trainType"] = trainType
            self.userDict[chat_id]["trainInfo"]["trainTypeShow"] = trainTypeShow
            self.userDict[chat_id]["lastAction"] = 10
            await self._send_seat_type_options(chat_id)
        else:
            # 잘못된 응답이면 키보드 다시 표시
            await self._send_train_type_options(chat_id)

        return None

    async def _send_seat_type_options(self, chat_id):
        """좌석 옵션 선택을 위해 인라인 키보드 전송"""
        keyboard = [
            [
                InlineKeyboardButton("일반실 우선 예약", callback_data="seat_type_1"),
                InlineKeyboardButton("일반실만 예약", callback_data="seat_type_2"),
            ],
            [
                InlineKeyboardButton("특실 우선 예약", callback_data="seat_type_3"),
                InlineKeyboardButton("특실만 예약", callback_data="seat_type_4"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=Messages.Info.INPUT_SEAT_TYPE,
            reply_markup=reply_markup,
        )

    async def _input_seat_type(self, chat_id, data):
        special_options = {
            "seat_type_1": (ReserveOption.GENERAL_FIRST, "일반실 우선 예약"),
            "seat_type_2": (ReserveOption.GENERAL_ONLY, "일반실만 예약"),
            "seat_type_3": (ReserveOption.SPECIAL_FIRST, "특실 우선 예약"),
            "seat_type_4": (ReserveOption.SPECIAL_ONLY, "특실만 예약"),
        }

        if data in special_options:
            specialInfo, specialInfoShow = special_options[data]
            self.userDict[chat_id]["trainInfo"]["specialInfo"] = specialInfo
            self.userDict[chat_id]["trainInfo"]["specialInfoShow"] = specialInfoShow
            self.userDict[chat_id]["lastAction"] = 11
            await self._send_confirm_reserve(chat_id)
        else:
            # 잘못된 응답이면 키보드 다시 표시
            await self._send_seat_type_options(chat_id)

        return None

    async def _send_confirm_reserve(self, chat_id):
        train_info = self.userDict[chat_id]["trainInfo"]
        msg = Messages.Info.CONFIRM_DETAILS.format(
            depDate=train_info["depDate"],
            srcLocate=train_info["srcLocate"],
            dstLocate=train_info["dstLocate"],
            depTime=train_info["depTime"],
            maxDepTime=train_info["maxDepTime"],
            trainTypeShow=train_info["trainTypeShow"],
            specialInfoShow=train_info["specialInfoShow"],
        )
        keyboard = [
            [
                InlineKeyboardButton("예", callback_data="confirm_yes"),
                InlineKeyboardButton("아니오", callback_data="confirm_no"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=reply_markup,
        )

    async def _start_reserve(self, chat_id, data):
        try:
            if data == "confirm_yes":
                self.userDict[chat_id]["lastAction"] = 12
                train_info = self.userDict[chat_id]["trainInfo"]
                user_info = self.userDict[chat_id]["userInfo"]

                arguments = [
                    user_info["korailId"],
                    user_info["korailPw"],
                    train_info["depDate"],
                    train_info["srcLocate"],
                    train_info["dstLocate"],
                    f"{train_info['depTime']}00",
                    train_info["trainType"],
                    train_info["specialInfo"],
                    chat_id,
                    train_info["maxDepTime"],
                ]
                arguments = [str(argument) for argument in arguments]
                print(f"Starting reservation, arguments: {arguments}")

                pid = self._start_background_process(arguments)

                self.userDict[chat_id]["pid"] = pid
                self.runningStatus[chat_id] = {
                    "pid": pid,
                    "korailId": user_info["korailId"],
                }

                # msgToSubscribers = f"{user_info['korailId']}의 {train_info['srcLocate']}에서 {train_info['dstLocate']}로 {train_info['depDate']}에 출발하는 열차 예약이 시작되었습니다."
                # self.sendToSubscribers(msgToSubscribers)

                msg = Messages.Info.RESERVE_STARTED
                await self.send_message(chat_id, msg)
            elif data == "confirm_no":
                self._reset_user_state(chat_id)
                msg = Messages.Error.RESERVE_CANCELLED
                await self.send_message(chat_id, msg)
            else:
                msg = Messages.Error.INPUT_WRONG
                keyboard = [
                    [
                        InlineKeyboardButton("예", callback_data="confirm_yes"),
                        InlineKeyboardButton("아니오", callback_data="confirm_no"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await self.send_message(chat_id, msg, reply_markup=reply_markup)
        except Exception as e:
            await self.send_message(
                chat_id,
                "예약 시작 중 오류가 발생했습니다. /start를 입력해 다시 시작해 주세요",
            )
            print(f"Error starting reservation, {chat_id}: {str(e)}")

    def _start_background_process(self, arguments):
        try:
            cmd = ["python", "-m", "telegramBot.worker"] + arguments
            cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd
            )

            return process.pid

        except Exception as e:
            print(f"Failed to start process: {str(e)}")
            return False, str(e)

    async def _already_doing(self, chat_id):
        train_info = self.userDict[chat_id]["trainInfo"]
        msg = Messages.Error.RESERVE_ALREADY_DOING.format(
            depDate=train_info["depDate"],
            srcLocate=train_info["srcLocate"],
            dstLocate=train_info["dstLocate"],
            depTime=train_info["depTime"],
            trainTypeShow=train_info["trainTypeShow"],
            specialInfoShow=train_info["specialInfoShow"],
        )
        await self.send_message(chat_id, msg)

    async def cancel_func(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        userPid = self.userDict[chat_id]["pid"]

        if chat_id not in self.runningStatus:
            msg = "진행중인 예약이 없습니다."
            await self.send_message(chat_id, msg)

        elif userPid != 9999999:
            os.kill(userPid, signal.SIGTERM)
            print(f"실행중인 프로세스 {userPid}를 종료합니다.")

            del self.runningStatus[chat_id]
            msgToSubscribers = f'{self.userDict[chat_id]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
            await self.broadcast_message(msgToSubscribers)

            self._reset_user_state(chat_id)
            msg = Messages.Info.RESERVE_FINISHED
            await self.send_message(chat_id, msg)

        return None

    async def subscribe_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        if chat_id not in self.subscribes:
            self.subscribes.append(chat_id)
            data = "열차 이용정보 구독 설정이 완료되었습니다."
        else:
            data = "이미 구독했습니다."
        await self.send_message(chat_id, data)

    async def broadcast_message(self, data):
        """Send message to all subscribers"""
        for chat_id in self.subscribes:
            await self.send_message(chat_id, data)

    async def get_status_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        count = len(self.runningStatus)
        usersKorailIds = [
            state["korailId"] for state in dict.values(self.runningStatus)
        ]
        data = f"총 {count}개의 예약이 실행중입니다. 이용중인 사용자 : {usersKorailIds}"
        await self.send_message(chat_id, data)

    async def cancel_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        count = len(self.runningStatus)
        pids = [state["pid"] for state in dict.values(self.runningStatus)]
        usersKorailIds = [
            state["korailId"] for state in dict.values(self.runningStatus)
        ]
        userschat_id = dict.keys(self.runningStatus)

        for pid in pids:
            os.kill(pid, signal.SIGTERM)
            print(f"프로세스 {pid}가 종료되었습니다.")

        dataForManager = f"총 {count}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {usersKorailIds}"
        await self.send_message(chat_id, dataForManager)

        dataForUser = Messages.Error.RESERVE_CANCELLED_BY_ADMIN
        for user in userschat_id:
            await self.send_message(user, dataForUser)
            self.handle_progress(user, 0)

        self.runningStatus = {}

    async def get_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        allUsers = [user["userInfo"]["korailId"] for user in dict.values(self.userDict)]
        data = f"총 {len(allUsers)}명의 유저가 있습니다 : {allUsers}"
        await self.send_message(chat_id, data)

    async def return_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        msg = """
- 예약 시작 : /start
- 예약 상태 확인 : /status
- 예약 진행 취소 : /cancel
        """
        await self.send_message(chat_id, msg)
