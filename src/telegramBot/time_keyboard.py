from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import datetime


def create_callback_data(action, time_str):
    """Callback data 형식 문자열 제작 (action;time)"""
    return ";".join([action, time_str])


def create_time_keyboard(
    action="time", start_hour=0, end_hour=24, interval=4, minute_interval=30
):
    """시간 선택을 위한 인라인 키보드 생성

    Args:
        action (str, optional): 콜백 액션 타입. 기본값 "time"
        start_hour (int, optional): 시작 시간 (0-23). 기본값 0
        end_hour (int, optional): 종료 시간 (0-24). 기본값 24
        interval (int, optional): 시간 간격. 기본값 4
        minute_interval (int, optional): 분 간격. 기본값 30

    Returns:
        InlineKeyboardMarkup: 시간 선택 레이아웃이 포함된 텔레그램 인라인 키보드 마크업
    """
    keyboard = []
    for hour in range(start_hour, end_hour, interval):
        row = []
        for minute in range(0, 60, minute_interval):
            time_str = f"{hour:02d}{minute:02d}"
            row.append(
                InlineKeyboardButton(
                    f"{hour:02d}:{minute:02d}",
                    callback_data=create_callback_data(action, time_str),
                )
            )
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def handle_time_action(query_data):
    """시간 선택 callback handler

    Args:
        query_data (str): 콜백 데이터 문자열 (action;time)

    Returns:
        tuple[bool, str | None]: 시간 선택 여부와 선택된 시간을 포함하는 튜플
            - 첫 번째 요소는 시간이 선택되었는지 여부(bool)
            - 두 번째 요소는 선택된 시간(HHMM 형식) 또는 None
    """
    ret_data = (False, None)
    try:
        action, time_str = query_data.split(";")
        if action in ["time", "maxtime"]:
            ret_data = True, time_str
    except ValueError:
        pass
    return ret_data
