from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import datetime


def create_callback_data(action, time_str):
    """Callback data 형식 문자열 제작 (action;time)"""
    return ";".join([action, time_str])


def create_time_keyboard(action="time", min_time=None):
    """시간 선택을 위한 인라인 키보드 생성

    Args:
        action (str, optional): 콜백 액션 타입. 기본값 "time"
        min_time (str, optional): 최소 시간 (HHMM 형식). None이면 06:00부터 시작

    Returns:
        InlineKeyboardMarkup: 시간 선택 레이아웃이 포함된 텔레그램 인라인 키보드 마크업
    """
    if min_time is None:
        # 일반 시간 선택 (출발시간)
        start_hour = 6
        end_hour = 24
        hour_interval = 2
        minute_interval = 30
        start_minute = 0
    else:
        # 최대 시간 선택 (최대 출발시간)
        min_hour = int(min_time[:2])
        min_minute = int(min_time[2:])
        start_hour = min_hour
        end_hour = 24
        hour_interval = 1
        minute_interval = 30
        start_minute = min_minute

    keyboard = []
    for hour in range(start_hour, end_hour, hour_interval):
        row = []
        if min_time and hour == start_hour:
            # 최소 시간이 있고 첫 번째 시간인 경우, 최소 분부터 시작
            current_start_minute = start_minute
            if current_start_minute % minute_interval != 0:
                # 시작 분이 interval에 맞지 않으면 다음 interval로 조정
                current_start_minute = (
                    (current_start_minute // minute_interval) + 1
                ) * minute_interval
            minute_range = range(current_start_minute, 60, minute_interval)
        else:
            minute_range = range(0, 60, minute_interval)

        for minute in minute_range:
            if hour < 24:  # 24시는 제외
                time_str = f"{hour:02d}{minute:02d}"
                row.append(
                    InlineKeyboardButton(
                        f"{hour:02d}:{minute:02d}",
                        callback_data=create_callback_data(action, time_str),
                    )
                )
        if row:  # 빈 행이 아닌 경우에만 추가
            keyboard.append(row)

    # 23:59 추가 (하루 끝) - maxtime인 경우에만
    if action == "maxtime" and (
        not min_time
        or int(min_time[:2]) < 23
        or (int(min_time[:2]) == 23 and int(min_time[2:]) < 59)
    ):
        keyboard.append(
            [
                InlineKeyboardButton(
                    "23:59",
                    callback_data=create_callback_data(action, "2359"),
                )
            ]
        )

    return InlineKeyboardMarkup(keyboard)


def create_max_time_keyboard(min_time):
    """최대 시간 선택을 위한 인라인 키보드 생성 (create_time_keyboard의 wrapper)

    Args:
        min_time (str): 최소 시간 (HHMM 형식)

    Returns:
        InlineKeyboardMarkup: 최대 시간 선택 레이아웃이 포함된 텔레그램 인라인 키보드 마크업
    """
    return create_time_keyboard(action="maxtime", min_time=min_time)


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
