###################################################################################################
# Original source: https://github.com/unmonoqueteclea/calendar-telegram
# Modified by: @raonsol
###################################################################################################
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
import datetime
import calendar


def create_callback_data(action, year, month, day):
    """Callback data 형식 문자열 제작 (action;year;month;day)"""
    return ";".join([action, str(year), str(month), str(day)])


def create_calendar(year=None, month=None):
    """제공된 연도와 월에 대한 인라인 키보드 달력 생성

    Args:
        year (int, optional): 달력에 표시할 연도, None일 경우 현재 연도로 기본 설정
        month (int, optional): 달력에 표시할 월(1-12), None일 경우 현재 월로 기본 설정

    Returns:
        InlineKeyboardMarkup: 달력 레이아웃이 포함된 텔레그램 인라인 키보드 마크업

    Example:
        >>> calendar_markup = create_calendar(2023, 12)
        >>> bot.send_message(chat_id, "날짜를 선택하세요:", reply_markup=calendar_markup)
    """
    now = datetime.datetime.now()
    if year == None:
        year = now.year
    if month == None:
        month = now.month
    data_ignore = create_callback_data("calendar_ignore", year, month, 0)
    keyboard = []
    # First row - Month and Year
    row = []
    row.append(
        InlineKeyboardButton(
            f"{str(year)} {month}월", callback_data=data_ignore
        )
    )
    keyboard.append(row)
    # Second row - Week Days
    row = []
    for day in ["일", "월", "화", "수", "목", "금", "토"]:
        row.append(InlineKeyboardButton(day, callback_data=data_ignore))
    keyboard.append(row)

    calendar.setfirstweekday(calendar.SUNDAY)
    my_calendar = calendar.monthcalendar(year, month)
    for week in my_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data=data_ignore))
            else:
                row.append(
                    InlineKeyboardButton(
                        str(day),
                        callback_data=create_callback_data(
                            "calendar_day", year, month, day
                        ),
                    )
                )
        keyboard.append(row)
    # Last row - Buttons
    row = []
    row.append(
        InlineKeyboardButton(
            "◀️", callback_data=create_callback_data("calendar_prev", year, month, day)
        )
    )
    row.append(InlineKeyboardButton(" ", callback_data=data_ignore))
    row.append(
        InlineKeyboardButton(
            "▶️", callback_data=create_callback_data("calendar_next", year, month, day)
        )
    )
    keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


async def handle_calendar_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """달력 인라인 키보드 callback handler

    이전/다음 버튼이 눌리면 새로운 달력을 생성하고, 날짜가 선택되면 해당 날짜를 반환

    Args:
        update (telegram.Update): CallbackQueryHandler가 제공하는 업데이트 객체
        context (telegram.ext.CallbackContext): CallbackQueryHandler가 제공하는 컨텍스트 객체

    Returns:
        tuple[bool, datetime.datetime | None]: 날짜 선택 여부와 선택된 날짜를 포함하는 튜플
            - 첫 번째 요소는 날짜가 선택되었는지 여부(bool)
            - 두 번째 요소는 선택된 날짜(datetime.datetime) 또는 None
    """
    ret_data = (False, None)
    query = update.callback_query
    print(query.data)
    (action, year, month, day) = query.data.split(";")
    curr = datetime.datetime(int(year), int(month), 1)

    if action == "calendar_ignore":
        await query.answer()
    elif action == "calendar_day":
        await context.bot.edit_message_text(
            text=query.message.text,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
        )
        ret_data = True, datetime.datetime(int(year), int(month), int(day))
    elif action == "calendar_prev":
        pre = curr - datetime.timedelta(days=1)
        await context.bot.edit_message_text(
            text=query.message.text,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            reply_markup=create_calendar(int(pre.year), int(pre.month)),
        )
    elif action == "calendar_next":
        ne = curr + datetime.timedelta(days=31)
        await context.bot.edit_message_text(
            text=query.message.text,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            reply_markup=create_calendar(int(ne.year), int(ne.month)),
        )
    else:
        await query.answer(text="Something went wrong!")

    return ret_data
