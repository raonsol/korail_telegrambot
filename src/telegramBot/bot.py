import logging
from datetime import datetime

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
from pydantic import ValidationError

from .korail_client import ReserveHandler
from .messages import Messages
from .calendar_keyboard import create_calendar, handle_calendar_action
from .time_keyboard import (
    create_time_keyboard,
    create_max_time_keyboard,
    handle_time_action,
    create_time_reselect_keyboard,
)
from .station_keyboard import search_stations, create_station_keyboard
from config import settings
from core.errors import ServiceError
from core.notifier import ReservationEvent
from core.schemas import (
    ADMIN_USER_ID,
    PHONE_FORMAT_HINT,
    SEAT_TYPE_LABELS,
    TRAIN_TYPE_LABELS,
    WAITLIST_SEAT_TYPES,
    Owner,
    ReservationRequest,
    ReservationStatus,
    format_phone,
    is_valid_phone,
    normalize_phone,
)

logger = logging.getLogger(__name__)


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
    today = datetime.today()
    return date_alt.date() == today.date()


def is_past_time(time: str):
    time_alt = datetime.strptime(time, "%H%M").time()
    current_time = datetime.now().time()
    return time_alt < current_time


class TelegramBot:
    """텔레그램 채널 어댑터

    대화 상태 머신(userDict)만 담당하고, 예약 실행/취소/조회는
    ``core.reservations.ReservationService`` 에 위임합니다.
    예약 결과 알림은 ``deliver()`` 로 받습니다 (Notifier 채널).
    """

    def __init__(self, token: str, services=None):
        self.token = token
        self.app = ApplicationBuilder().token(self.token).build()
        self._register_handlers()
        self.lastSentMessage = None
        self.services = services

    # userDict : 대화 진행 상태 (메모리)
    # {
    #   123123: {
    #     "inProgress": True,
    #     "lastAction": 4,
    #     "userInfo": {"korailId": "010-1111-1111", "korailPw": "...",
    #                  "ownerId": "01011111111", "isAdmin": False},
    #     "trainInfo": {"srcLocate": "광명", "dstLocate": "광주송정", "depDate": "20210204"},
    #   }
    # }
    userDict = {}

    # Group for get notification
    subscribes = []

    @property
    def reservations(self):
        return self.services.reservations

    @property
    def users(self):
        return self.services.users

    async def set_webhook(self, url):
        try:
            result = await self.app.bot.set_webhook(url)
            return result
        except Exception as e:
            raise e

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
            "users": self.list_registered_users,
            "adduser": self.add_registered_user,
            "deluser": self.delete_registered_user,
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
            13: self._input_waitlist,
            11: self._start_reserve,
        }

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
        elif query.data.startswith("waitlist_"):
            await self._input_waitlist(chat_id, query.data)
        elif query.data.startswith("confirm_"):
            await self._start_reserve(chat_id, query.data)
        elif query.data.startswith("calendar_"):
            selected, date = await handle_calendar_action(update, context)
            if selected:
                await self._input_date(chat_id, date)
        elif query.data.startswith("station_src;"):
            station_name = query.data.split(";", 1)[1]
            await self._select_src_station(chat_id, station_name)
        elif query.data.startswith("station_dst;"):
            station_name = query.data.split(";", 1)[1]
            await self._select_dst_station(chat_id, station_name)
        elif query.data.startswith("stn_page;"):
            _, action, search_query, page = query.data.split(";")
            await self._search_and_show_stations(
                chat_id, search_query, action, int(page)
            )
        elif query.data.startswith("login_"):
            await self._handle_login_callback(chat_id, query.data)
        elif query.data.startswith("cancel_"):
            await self._handle_cancel_callback(chat_id, query.data)
        else:
            selected, time_result = handle_time_action(query.data)
            if selected:
                if time_result == "reselect":
                    # 시간 선택 다시하기
                    self.userDict[chat_id]["lastAction"] = 7
                    msg = (
                        "시간 선택을 다시 시작합니다.\n\n"
                        + Messages.Info.INPUT_DEP_TIME
                    )
                    current_time = datetime.now().strftime("%H%M")
                    await self.send_message(
                        chat_id,
                        msg,
                        reply_markup=create_time_keyboard(
                            action="time", min_time=current_time
                        ),
                    )
                elif query.data.startswith("time;"):
                    await self._input_dep_time(chat_id, time_result)
                elif query.data.startswith("maxtime;"):
                    await self._input_max_dep_time(chat_id, time_result)

    async def _handle_login_callback(self, chat_id, callback_data):
        """로그인 실패 후 사용자 선택 처리"""
        if callback_data == "login_back":
            # 전화번호 입력 단계로 돌아가기
            self.userDict[chat_id]["userInfo"]["korailId"] = "no-login-yet"
            self.userDict[chat_id]["userInfo"]["korailPw"] = "no-login-yet"
            self.userDict[chat_id]["lastAction"] = 2
            msg = Messages.Info.INPUT_ID
            await self.send_message(chat_id, msg)

    async def _handle_cancel_callback(self, chat_id, callback_data):
        """Handle cancel reservation callback"""
        if callback_data == "cancel_all":
            success = await self._cancel_reservation(chat_id)
            if success:
                msg = "모든 예약이 취소되었습니다."
            else:
                msg = "예약 취소 중 오류가 발생했습니다."
            await self.send_message(chat_id, msg)

        elif callback_data == "cancel_back":
            msg = "취소 작업이 중단되었습니다."
            await self.send_message(chat_id, msg)

        else:
            reservation_id = callback_data.replace("cancel_", "", 1)
            success = await self._cancel_reservation(chat_id, reservation_id)
            if success:
                msg = "선택한 예약이 취소되었습니다."
            else:
                msg = "예약 취소 중 오류가 발생했습니다."
            await self.send_message(chat_id, msg)

    def _reset_user_state(self, chat_id):
        self.userDict[chat_id]["inProgress"] = False
        self.userDict[chat_id]["lastAction"] = 0
        self.userDict[chat_id]["trainInfo"] = {}

    def _create_user(self, chat_id):
        self.userDict[chat_id] = {
            "inProgress": False,
            "lastAction": 0,
            "userInfo": {
                "korailId": "no-login-yet",
                "korailPw": "no-login-yet",
            },
            "trainInfo": {},
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
            # print(f"Send message to {chat_id} : {text}")
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
        if data == settings.admin_password:
            username = settings.admin_korail_id
            password = settings.admin_korail_pw

            if not (username and password):
                self._reset_user_state(chat_id)
                msg = "관리자 계정 정보가 설정되지 않았습니다."
                await self.send_message(chat_id, msg)
                return None

            self.userDict[chat_id]["userInfo"].update(
                {
                    "korailId": username,
                    "korailPw": password,
                    "ownerId": ADMIN_USER_ID,
                    "isAdmin": True,
                }
            )

            reserve_handler = ReserveHandler(self.users.korail_device(username))
            loginSuc = reserve_handler.login(username, password)
            reserve_handler.close()
            if loginSuc:
                msg = Messages.Info.INPUT_DATE
                self.userDict[chat_id]["lastAction"] = 4
                await self.send_message(chat_id, msg, reply_markup=create_calendar())
            else:
                self._reset_user_state(chat_id)
                msg = f"""관리자 계정으로 로그인에 실패하였습니다.
사유 : {reserve_handler.loginError}

ADMIN_KORAIL_ID / ADMIN_KORAIL_PW 설정을 확인해주세요."""
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
        normalized_data = normalize_phone(data)

        # 하이픈 유무와 관계없이 010 휴대폰 번호면 허용
        if not is_valid_phone(data):
            msg = f"올바른 전화번호 형식을 입력해주세요. ({PHONE_FORMAT_HINT})"
        elif not self.users.is_allowed(normalized_data):
            msgToSubscribers = f"{data}는 등록되지 않은 사용자입니다."
            await self.broadcast_message(msgToSubscribers)
            self._reset_user_state(chat_id)
            msg = "등록되지 않은 사용자입니다."
        else:
            # Format phone number with hyphens for Korail API
            self.userDict[chat_id]["userInfo"]["korailId"] = format_phone(
                normalized_data
            )
            self.userDict[chat_id]["userInfo"]["ownerId"] = normalized_data
            self.userDict[chat_id]["userInfo"]["isAdmin"] = False
            self.userDict[chat_id]["lastAction"] = 3
            msg = Messages.Info.INPUT_PW
        await self.send_message(chat_id, msg)
        return None

    # 패스워드 입력 함수
    async def _input_pw(self, chat_id, data):
        self.userDict[chat_id]["userInfo"]["korailPw"] = data
        username = self.userDict[chat_id]["userInfo"]["korailId"]
        password = self.userDict[chat_id]["userInfo"]["korailPw"]
        reserve_handler = ReserveHandler(self.users.korail_device(username))
        loginSuc = reserve_handler.login(username, password)
        reserve_handler.close()
        if loginSuc:
            owner_id = self.userDict[chat_id]["userInfo"].get("ownerId")
            if owner_id:
                # 웹에서 시작한 예약 결과도 이 채팅으로 받을 수 있도록 연결
                self.users.link_telegram(owner_id, chat_id)
            msg = Messages.Info.INPUT_DATE
            self.userDict[chat_id]["lastAction"] = 4
            await self.send_message(chat_id, msg, reply_markup=create_calendar())
        else:
            # 로그인 실패 시 비밀번호 재입력 또는 뒤로가기 선택지 제공
            msg = f"""로그인에 실패하였습니다.
사유 : {reserve_handler.loginError}

로그인에 사용한 정보는 다음과 같습니다.
==============
아이디 : {username}
==============

비밀번호를 다시 입력하거나 "뒤로 돌아가기"를 선택해주세요.

5회 이상 로그인 실패할 경우, 홈페이지를 통해 비밀번호를 재설정하셔야합니다."""

            keyboard = [
                [InlineKeyboardButton("뒤로 돌아가기", callback_data="login_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_message(chat_id, msg, reply_markup=reply_markup)

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
            formatted_date = data.strftime("%Y년 %m월 %d일")
            self.userDict[chat_id]["trainInfo"]["depDate"] = data.strftime("%Y%m%d")
            self.userDict[chat_id]["lastAction"] = 5
            msg = (
                f"선택하신 날짜: {formatted_date}\n\n{Messages.Info.INPUT_SRC_STATION}"
            )
            await self.send_message(chat_id, msg)
        else:
            msg = Messages.Error.INPUT_DATE_FAILURE
            await self.send_message(chat_id, msg, reply_markup=create_calendar())
        return None

    async def _input_src_station(self, chat_id, data):
        await self._search_and_show_stations(chat_id, data, "station_src")
        return None

    async def _input_dst_station(self, chat_id, data):
        await self._search_and_show_stations(chat_id, data, "station_dst")
        return None

    async def _search_and_show_stations(self, chat_id, query, action, page=1):
        """역 이름 검색 후 인라인 키보드로 결과 표시"""
        result = await search_stations(query, page)
        stations = result["stations"]
        total = result["total"]

        if not stations:
            msg = Messages.Info.STATION_SEARCH_NO_RESULT.format(query=query)
            await self.send_message(chat_id, msg)
            return

        label = "출발역" if action == "station_src" else "도착역"
        msg = f"'{query}' 검색 결과 ({total}건)\n{label}을 선택해주세요."
        keyboard = create_station_keyboard(stations, action, query, page, total)
        await self.send_message(chat_id, msg, reply_markup=keyboard)

    async def _select_src_station(self, chat_id, station_name):
        """출발역 선택 완료 처리"""
        self.userDict[chat_id]["trainInfo"]["srcLocate"] = station_name
        self.userDict[chat_id]["lastAction"] = 6
        msg = f"선택하신 출발역: {station_name}\n\n{Messages.Info.INPUT_DST_STATION}"
        await self.send_message(chat_id, msg)

    async def _select_dst_station(self, chat_id, station_name):
        """도착역 선택 완료 처리"""
        self.userDict[chat_id]["trainInfo"]["dstLocate"] = station_name
        self.userDict[chat_id]["lastAction"] = 7
        msg = f"선택하신 도착역: {station_name}\n\n{Messages.Info.INPUT_DEP_TIME}"

        current_time = datetime.now().strftime("%H%M")
        if self.userDict[chat_id]["trainInfo"]["depDate"] == datetime.now().strftime(
            "%Y%m%d"
        ):
            min_time = current_time
        else:
            min_time = None
        await self.send_message(
            chat_id,
            msg,
            reply_markup=create_time_keyboard(action="time", min_time=min_time),
        )

    async def _input_dep_time(self, chat_id, data):
        dep_date = self.userDict[chat_id]["trainInfo"]["depDate"]
        if not is_valid_time(str(data)):
            msg = Messages.Error.INPUT_DEP_TIME_FAILURE
        elif is_today(dep_date) and is_past_time(str(data)):
            msg = Messages.Error.INPUT_DEP_TIME_PAST_FAILURE
        else:
            self.userDict[chat_id]["trainInfo"]["depTime"] = data
            self.userDict[chat_id]["lastAction"] = 8
            msg = f"선택하신 출발 시간: {data[:2]}:{data[2:]}\n\n{Messages.Info.INPUT_MAX_DEP_TIME}"
            await self.send_message(
                chat_id,
                msg,
                reply_markup=create_time_keyboard(action="maxtime", min_time=data),
            )
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

            # 시간 범위 메시지 생성
            start_formatted = f"{dep_time[:2]}:{dep_time[2:]}"
            end_formatted = f"{data[:2]}:{data[2:]}"
            time_range_msg = f"검색 시간 범위: {start_formatted} ~ {end_formatted}"

            await self._send_train_type_options(chat_id, time_range_msg)
        return None

    async def _send_train_type_options(self, chat_id, prefix_msg=""):
        """기차 옵션 선택을 위해 인라인 키보드 전송"""
        keyboard = [
            [
                InlineKeyboardButton("KTX", callback_data="train_type_1"),
                InlineKeyboardButton("모든 열차", callback_data="train_type_2"),
            ],
            [
                InlineKeyboardButton(
                    "⬅️시간 다시 선택하기", callback_data="reselect_time"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=prefix_msg + Messages.Info.INPUT_TRAIN_TYPE,
            reply_markup=reply_markup,
        )

    async def _input_train_type(self, chat_id, data):
        train_type_map = {
            "train_type_1": ("KTX", TRAIN_TYPE_LABELS["KTX"]),
            "train_type_2": ("ALL", TRAIN_TYPE_LABELS["ALL"]),
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
            "seat_type_1": ("general", SEAT_TYPE_LABELS["general"]),
            "seat_type_2": ("general_only", SEAT_TYPE_LABELS["general_only"]),
            "seat_type_3": ("special", SEAT_TYPE_LABELS["special"]),
            "seat_type_4": ("special_only", SEAT_TYPE_LABELS["special_only"]),
        }

        if data in special_options:
            specialInfo, specialInfoShow = special_options[data]
            self.userDict[chat_id]["trainInfo"]["specialInfo"] = specialInfo
            self.userDict[chat_id]["trainInfo"]["specialInfoShow"] = specialInfoShow
            if specialInfo in WAITLIST_SEAT_TYPES:
                # 예약대기 사용 여부 선택 (13) 후 확인 (11)
                self.userDict[chat_id]["lastAction"] = 13
                await self._send_waitlist_options(chat_id)
            else:
                # 특실만 예약은 예약대기를 쓸 수 없음 (코레일 대기 여부가 일반실 기준)
                self._set_waitlist(chat_id, False)
                self.userDict[chat_id]["lastAction"] = 11
                await self._send_confirm_reserve(chat_id)
        else:
            # 잘못된 응답이면 키보드 다시 표시
            await self._send_seat_type_options(chat_id)

        return None

    async def _send_waitlist_options(self, chat_id):
        """예약대기 사용 여부 선택 (설명 포함)"""
        keyboard = [
            [
                InlineKeyboardButton("예약대기 사용", callback_data="waitlist_on"),
                InlineKeyboardButton("사용 안 함", callback_data="waitlist_off"),
            ],
        ]
        await self.send_message(
            chat_id=chat_id,
            text=Messages.Info.INPUT_WAITLIST,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    def _set_waitlist(self, chat_id, enabled: bool):
        train_info = self.userDict[chat_id]["trainInfo"]
        train_info["allowWaitlist"] = enabled
        if enabled:
            train_info["waitlistShow"] = "사용 (모두 매진이면 일반실 예약대기 신청)"
        elif train_info.get("specialInfo") in WAITLIST_SEAT_TYPES:
            train_info["waitlistShow"] = "사용 안 함"
        else:
            train_info["waitlistShow"] = "사용 안 함 (특실만 예약)"

    async def _input_waitlist(self, chat_id, data):
        if self.userDict.get(chat_id, {}).get("lastAction") not in (13, 11):
            return None  # 이전 단계의 버튼 (예약 진행 중 등) - 무시
        if data == "waitlist_on" or is_affirmative(data):
            enabled = True
        elif data == "waitlist_off" or is_negative(data):
            enabled = False
        else:
            # 잘못된 응답이면 키보드 다시 표시
            await self._send_waitlist_options(chat_id)
            return None

        self._set_waitlist(chat_id, enabled)
        self.userDict[chat_id]["lastAction"] = 11
        await self._send_confirm_reserve(chat_id)
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
            waitlistShow=train_info.get("waitlistShow", "사용 안 함"),
        )

        keyboard = [
            [
                InlineKeyboardButton("예", callback_data="confirm_yes"),
                InlineKeyboardButton("아니오", callback_data="confirm_no"),
            ],
            [
                InlineKeyboardButton(
                    "⬅️시간 다시 선택하기", callback_data="reselect_time"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=reply_markup,
        )

    def _chat_owner(self, chat_id) -> Owner | None:
        """이 채팅에서 로그인한 사용자 (재시작 후에는 DB의 텔레그램 연결 정보 사용)"""
        user_info = self.userDict.get(chat_id, {}).get("userInfo", {})
        owner_id = user_info.get("ownerId")
        if owner_id:
            return Owner(user_id=owner_id, is_admin=bool(user_info.get("isAdmin")))
        linked = self.users.owner_for_chat(chat_id)
        return Owner(user_id=linked) if linked else None

    def _active_reservations(self, chat_id):
        owner = self._chat_owner(chat_id)
        if owner and owner.is_admin:
            # 관리자는 본인(admin) 예약 + 이 채팅의 예약만 (전체는 /status, /cancelall)
            owner = Owner(user_id=ADMIN_USER_ID)
        return self.reservations.list(owner, chat_id=chat_id, active=True)

    async def _start_reserve(self, chat_id, data):
        try:
            if data == "confirm_yes":
                train_info = self.userDict[chat_id]["trainInfo"]
                user_info = self.userDict[chat_id]["userInfo"]
                owner = self._chat_owner(chat_id)
                if owner is None:
                    await self.send_message(
                        chat_id,
                        "로그인 정보가 없습니다. /start를 입력해 다시 시작해 주세요",
                    )
                    return

                request = ReservationRequest(
                    dep_date=train_info["depDate"],
                    src_station=train_info["srcLocate"],
                    dst_station=train_info["dstLocate"],
                    dep_time=train_info["depTime"],
                    max_dep_time=train_info["maxDepTime"],
                    train_type=train_info["trainType"],
                    seat_type=train_info["specialInfo"],
                    allow_waitlist=train_info.get("allowWaitlist", False),
                )
                reservation = await self.reservations.start(
                    owner,
                    request,
                    user_info["korailId"],
                    user_info["korailPw"],
                    origin="telegram",
                    chat_id=chat_id,
                )
                self.userDict[chat_id]["lastAction"] = 12
                logger.info(f"Started reservation {reservation.id} for chat {chat_id}")

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
        except ValidationError as e:
            reason = e.errors()[0].get("msg", "").replace("Value error, ", "")
            await self.send_message(
                chat_id,
                f"입력값을 확인해주세요: {reason}\n/start를 입력해 다시 시작해 주세요",
            )
        except ServiceError as e:
            await self.send_message(chat_id, e.message)
        except Exception as e:
            await self.send_message(
                chat_id,
                "예약 시작 중 오류가 발생했습니다. /start를 입력해 다시 시작해 주세요",
            )
            logger.error(f"Error starting reservation, {chat_id}: {str(e)}")

    async def _show_cancel_menu(self, chat_id):
        """Show list of ongoing reservations for user to cancel"""
        try:
            user_reservations = self._active_reservations(chat_id)

            if not user_reservations:
                await self.send_message(chat_id, "진행 중인 예약이 없습니다.")
                return False

            keyboard = []
            msg_lines = ["진행 중인 예약 목록:\n"]

            for idx, r in enumerate(user_reservations, 1):
                label = (
                    f"{idx}. {r.dep_date[4:6]}/{r.dep_date[6:]} "
                    f"{r.src_station}→{r.dst_station} "
                    f"{r.dep_time[:2]}:{r.dep_time[2:]}~"
                    f"{r.max_dep_time[:2]}:{r.max_dep_time[2:]}"
                )
                msg_lines.append(label)
                keyboard.append(
                    [InlineKeyboardButton(label, callback_data=f"cancel_{r.id}")]
                )

            keyboard.append(
                [InlineKeyboardButton("🗑️ 모두 취소", callback_data="cancel_all")]
            )
            keyboard.append(
                [InlineKeyboardButton("⬅️ 뒤로가기", callback_data="cancel_back")]
            )

            reply_markup = InlineKeyboardMarkup(keyboard)
            msg = "\n".join(msg_lines)
            await self.send_message(chat_id, msg, reply_markup=reply_markup)
            return True

        except Exception as e:
            logger.error(f"Error showing cancel menu for {chat_id}: {e}")
            return False

    async def _cancel_reservation(self, chat_id, reservation_id=None):
        """Cancel specific reservation or all reservations for this chat

        Args:
            chat_id: User's chat ID
            reservation_id: Specific reservation to cancel. If None, cancel all.
        """
        try:
            owner = self._chat_owner(chat_id)
            if owner and owner.is_admin:
                owner = Owner(user_id=ADMIN_USER_ID)
            if reservation_id:
                await self.reservations.cancel(
                    reservation_id, owner=owner, chat_id=chat_id, source="telegram"
                )
                cancelled = 1
            else:
                cancelled = len(
                    await self.reservations.cancel_many(
                        owner=owner, chat_id=chat_id, source="telegram"
                    )
                )
                if cancelled == 0:
                    return False

            if chat_id in self.userDict and not self._active_reservations(chat_id):
                self._reset_user_state(chat_id)

            logger.info(f"Cancelled {cancelled} reservation(s) for chat_id: {chat_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling reservation for {chat_id}: {e}")
            return False

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

        if not self._active_reservations(chat_id):
            msg = "진행중인 예약이 없습니다."
            await self.send_message(chat_id, msg)
            return None

        try:
            # Show cancel menu for user to choose
            await self._show_cancel_menu(chat_id)

        except Exception as e:
            print(f"예약 취소 메뉴 표시 중 오류 발생: {str(e)}")
            msg = "예약 취소 중 오류가 발생했습니다. 관리자에게 문의하세요."
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
        if not self._is_admin_chat(chat_id):
            # 일반 사용자는 본인 예약만 (다른 사용자의 전화번호를 보여주지 않음)
            mine = self._active_reservations(chat_id)
            await self.send_message(
                chat_id, f"진행 중인 내 예약은 {len(mine)}개입니다."
            )
            return
        active = self.reservations.list(
            Owner(user_id=ADMIN_USER_ID, is_admin=True), active=True, scope_all=True
        )
        usersKorailIds = sorted({format_phone(r.owner_id) for r in active})
        data = f"총 {len(active)}개의 예약이 실행중입니다. 이용중인 사용자 : {usersKorailIds}"
        await self.send_message(chat_id, data)

    async def cancel_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        # 텔레그램·웹의 모든 사용자 예약을 취소하므로 관리자만
        if not await self._require_admin(chat_id):
            return
        admin = Owner(user_id=ADMIN_USER_ID, is_admin=True)
        active = self.reservations.list(admin, active=True, scope_all=True)
        usersKorailIds = sorted({format_phone(r.owner_id) for r in active})

        # 사용자에게는 deliver()에서 RESERVE_CANCELLED_BY_ADMIN 메시지가 전송됨
        cancelled = await self.reservations.cancel_many(
            owner=admin, scope_all=True, source="admin"
        )

        dataForManager = f"총 {len(cancelled)}/{len(active)}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {usersKorailIds}"
        await self.send_message(chat_id, dataForManager)

    async def get_all_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        if not await self._require_admin(chat_id):
            return
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

    # ------------------------------------------------------------------ 사용자 관리 (관리자)

    def _is_admin_chat(self, chat_id) -> bool:
        return bool(self.userDict.get(chat_id, {}).get("userInfo", {}).get("isAdmin"))

    async def _require_admin(self, chat_id) -> bool:
        if self._is_admin_chat(chat_id):
            return True
        await self.send_message(
            chat_id,
            "관리자만 사용할 수 있는 명령입니다. /start 후 관리자 비밀번호로 로그인해주세요.",
        )
        return False

    async def list_registered_users(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        if not await self._require_admin(chat_id):
            return
        users = self.users.list()
        lines = [f"등록된 사용자 {len(users)}명"]
        for u in users:
            state = "" if u.is_active else " (비활성)"
            linked = " 📱" if u.telegram_chat_id else ""
            lines.append(f"- {format_phone(u.id)} {u.name or ''}{state}{linked}")
        await self.send_message(chat_id, "\n".join(lines))

    async def add_registered_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/adduser 010-1234-5678 [이름]"""
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        if not await self._require_admin(chat_id):
            return
        args = list(getattr(context, "args", None) or [])
        if not args:
            await self.send_message(chat_id, "사용법: /adduser 010-1234-5678 [이름]")
            return
        try:
            user = self.users.create(args[0], " ".join(args[1:]) or None)
            msg = f"{format_phone(user.id)} 사용자를 등록했습니다."
        except ServiceError as e:
            msg = e.message
        await self.send_message(chat_id, msg)

    async def delete_registered_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """/deluser 010-1234-5678"""
        chat_id = update.message.chat_id
        self.ensure_user_exists(chat_id)
        if not await self._require_admin(chat_id):
            return
        args = list(getattr(context, "args", None) or [])
        if not args:
            await self.send_message(chat_id, "사용법: /deluser 010-1234-5678")
            return
        try:
            self.users.delete(args[0])
            self.services.auth.revoke_user_sessions(normalize_phone(args[0]))
            msg = f"{format_phone(args[0])} 사용자를 삭제했습니다."
        except ServiceError as e:
            msg = e.message
        await self.send_message(chat_id, msg)

    # ------------------------------------------------------------------ 알림 채널

    def _notification_targets(self, event: ReservationEvent) -> set:
        targets = set()
        if event.chat_id:
            targets.add(event.chat_id)
        linked = self.users.telegram_target(event.reservation.owner_id)
        if linked:
            targets.add(linked)
        return targets

    @staticmethod
    def _notification_message(event: ReservationEvent) -> str | None:
        r = event.reservation
        status = r.status
        if status == ReservationStatus.SUCCESS:
            template = (
                Messages.Info.WAITLIST_SUCCESS
                if r.waitlisted
                else Messages.Info.RESERVE_SUCCESS
            )
            msg = template.format(reserveInfo=r.result_text or "")
        elif status == ReservationStatus.FAILED:
            msg = Messages.Error.RESERVE_FAILED
        elif status == ReservationStatus.ERROR:
            msg = Messages.Error.RESERVE_WRONG
            if r.error:
                msg += f"\n(사유: {r.error})"
        elif status == ReservationStatus.CANCELLED:
            if event.source == "admin":
                msg = Messages.Error.RESERVE_CANCELLED_BY_ADMIN
            else:
                msg = Messages.Info.RESERVE_FINISHED
        else:
            return None

        if r.origin == "web" or status == ReservationStatus.CANCELLED:
            route = (
                f"[{'웹' if r.origin == 'web' else '텔레그램'} 예약] "
                f"{r.dep_date[4:6]}/{r.dep_date[6:]} {r.src_station}→{r.dst_station}"
            )
            msg = f"{route}\n{msg}"
        return msg

    async def deliver(self, event: ReservationEvent) -> None:
        """예약이 종료되면 텔레그램으로 결과 전송 (Notifier 채널)"""
        if not event.is_terminal_change:
            return
        # 텔레그램에서 직접 취소한 경우 이미 응답했으므로 생략
        if (
            event.reservation.status == ReservationStatus.CANCELLED
            and event.source == "telegram"
        ):
            return
        msg = self._notification_message(event)
        if not msg:
            return
        for chat_id in self._notification_targets(event):
            await self.send_message(chat_id, msg)
            state = self.userDict.get(chat_id)
            if (
                state
                and state.get("lastAction") == 12
                and not self._active_reservations(chat_id)
            ):
                self._reset_user_state(chat_id)
