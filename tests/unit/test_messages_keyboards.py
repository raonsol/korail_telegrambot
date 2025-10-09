"""
Unit tests for messages and keyboard utilities
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime
from telegram import InlineKeyboardMarkup, InlineKeyboardButton


class TestMessages:
    """Test Messages class"""

    def test_messages_info_constants(self):
        """Test that all Info messages are defined"""
        from telegramBot.messages import Messages

        assert Messages.Info.START_MESSAGE
        assert Messages.Info.INPUT_ID
        assert Messages.Info.INPUT_PW
        assert Messages.Info.INPUT_DATE
        assert Messages.Info.INPUT_SRC_STATION
        assert Messages.Info.INPUT_DST_STATION
        assert Messages.Info.INPUT_DEP_TIME
        assert Messages.Info.INPUT_MAX_DEP_TIME
        assert Messages.Info.INPUT_TRAIN_TYPE
        assert Messages.Info.INPUT_SEAT_TYPE
        assert Messages.Info.CONFIRM_DETAILS
        assert Messages.Info.RESERVE_STARTED
        assert Messages.Info.RESERVE_SUCCESS
        assert Messages.Info.RESERVE_FINISHED

    def test_messages_error_constants(self):
        """Test that all Error messages are defined"""
        from telegramBot.messages import Messages

        assert Messages.Error.RESERVE_CANCELLED
        assert Messages.Error.RESERVE_CANCELLED_BY_ADMIN
        assert Messages.Error.RESERVE_WRONG
        assert Messages.Error.RESERVE_FAILED
        assert Messages.Error.INPUT_WRONG
        assert Messages.Error.INPUT_DATE_FAILURE
        assert Messages.Error.INPUT_DEP_TIME_FAILURE
        assert Messages.Error.INPUT_DEP_TIME_PAST_FAILURE
        assert Messages.Error.INPUT_DEP_TIME_MAX_PAST_FAILURE
        assert Messages.Error.RESERVE_ALREADY_DOING

    def test_confirm_details_formatting(self):
        """Test CONFIRM_DETAILS message formatting"""
        from telegramBot.messages import Messages

        formatted = Messages.Info.CONFIRM_DETAILS.format(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="0900",
            maxDepTime="1200",
            trainTypeShow="KTX",
            specialInfoShow="일반실 우선 예약",
        )

        assert "20250115" in formatted
        assert "서울" in formatted
        assert "부산" in formatted
        assert "0900" in formatted
        assert "1200" in formatted
        assert "KTX" in formatted
        assert "일반실 우선 예약" in formatted

    def test_reserve_success_formatting(self):
        """Test RESERVE_SUCCESS message formatting"""
        from telegramBot.messages import Messages

        train_info = "KTX 001 (09:00~11:30)"
        formatted = Messages.Info.RESERVE_SUCCESS.format(reserveInfo=train_info)

        assert train_info in formatted
        assert "20분내에" in formatted
        assert "letskorail.com" in formatted

    def test_reserve_already_doing_formatting(self):
        """Test RESERVE_ALREADY_DOING message formatting"""
        from telegramBot.messages import Messages

        formatted = Messages.Error.RESERVE_ALREADY_DOING.format(
            depDate="20250115",
            srcLocate="서울",
            dstLocate="부산",
            depTime="0900",
            trainTypeShow="KTX",
            specialInfoShow="일반실 우선 예약",
        )

        assert "20250115" in formatted
        assert "서울" in formatted
        assert "부산" in formatted


class TestCalendarKeyboard:
    """Test calendar keyboard functionality"""

    def test_create_callback_data(self):
        """Test callback data creation"""
        from telegramBot.calendar_keyboard import create_callback_data

        callback = create_callback_data("calendar_day", 2025, 1, 15)

        assert callback == "calendar_day;2025;1;15"

    def test_create_calendar_default(self):
        """Test calendar creation with default (current) date"""
        from telegramBot.calendar_keyboard import create_calendar

        calendar_markup = create_calendar()

        assert isinstance(calendar_markup, InlineKeyboardMarkup)
        assert len(calendar_markup.inline_keyboard) > 0

        # First row should contain month/year
        first_row = calendar_markup.inline_keyboard[0]
        assert len(first_row) == 1

        # Second row should contain weekdays
        second_row = calendar_markup.inline_keyboard[1]
        assert len(second_row) == 7

    def test_create_calendar_specific_date(self):
        """Test calendar creation with specific date"""
        from telegramBot.calendar_keyboard import create_calendar

        calendar_markup = create_calendar(2025, 1)

        assert isinstance(calendar_markup, InlineKeyboardMarkup)

        # First row contains month/year
        first_row = calendar_markup.inline_keyboard[0]
        month_year_button = first_row[0]
        assert "2025" in month_year_button.text
        assert "1월" in month_year_button.text

    def test_create_calendar_navigation_buttons(self):
        """Test calendar has navigation buttons"""
        from telegramBot.calendar_keyboard import create_calendar

        calendar_markup = create_calendar(2025, 1)

        # Last row should have prev/next buttons
        last_row = calendar_markup.inline_keyboard[-1]
        assert len(last_row) == 3
        assert "◀️" in last_row[0].text
        assert "▶️" in last_row[2].text

    @pytest.mark.asyncio
    async def test_handle_calendar_action_day_select(self):
        """Test calendar day selection"""
        from telegramBot.calendar_keyboard import handle_calendar_action

        # Mock update and context
        update = Mock()
        update.callback_query = Mock()
        update.callback_query.data = "calendar_day;2025;1;15"
        update.callback_query.message = Mock()
        update.callback_query.message.chat_id = 123456
        update.callback_query.message.message_id = 1
        update.callback_query.message.text = "Select a date"

        context = Mock()
        context.bot = Mock()
        context.bot.edit_message_text = AsyncMock()

        selected, date = await handle_calendar_action(update, context)

        assert selected is True
        assert isinstance(date, datetime)
        assert date.year == 2025
        assert date.month == 1
        assert date.day == 15

    @pytest.mark.asyncio
    async def test_handle_calendar_action_ignore(self):
        """Test calendar ignore action"""
        from telegramBot.calendar_keyboard import handle_calendar_action

        update = Mock()
        update.callback_query = Mock()
        update.callback_query.data = "calendar_ignore;2025;1;0"
        update.callback_query.answer = AsyncMock()

        context = Mock()

        selected, date = await handle_calendar_action(update, context)

        assert selected is False
        assert date is None
        update.callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_calendar_action_prev_month(self):
        """Test calendar previous month navigation"""
        from telegramBot.calendar_keyboard import handle_calendar_action

        update = Mock()
        update.callback_query = Mock()
        update.callback_query.data = "calendar_prev;2025;2;1"
        update.callback_query.message = Mock()
        update.callback_query.message.chat_id = 123456
        update.callback_query.message.message_id = 1
        update.callback_query.message.text = "Select a date"

        context = Mock()
        context.bot = Mock()
        context.bot.edit_message_text = AsyncMock()

        selected, date = await handle_calendar_action(update, context)

        assert selected is False
        assert date is None
        context.bot.edit_message_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_calendar_action_next_month(self):
        """Test calendar next month navigation"""
        from telegramBot.calendar_keyboard import handle_calendar_action

        update = Mock()
        update.callback_query = Mock()
        update.callback_query.data = "calendar_next;2025;1;31"
        update.callback_query.message = Mock()
        update.callback_query.message.chat_id = 123456
        update.callback_query.message.message_id = 1
        update.callback_query.message.text = "Select a date"

        context = Mock()
        context.bot = Mock()
        context.bot.edit_message_text = AsyncMock()

        selected, date = await handle_calendar_action(update, context)

        assert selected is False
        assert date is None
        context.bot.edit_message_text.assert_called_once()


class TestTimeKeyboard:
    """Test time selection keyboard functionality"""

    def test_create_time_keyboard(self):
        """Test time keyboard creation"""
        from telegramBot.time_keyboard import create_time_keyboard

        keyboard = create_time_keyboard(action="time")

        assert isinstance(keyboard, InlineKeyboardMarkup)
        assert len(keyboard.inline_keyboard) > 0

    def test_create_time_keyboard_with_min_time(self):
        """Test time keyboard with minimum time constraint"""
        from telegramBot.time_keyboard import create_time_keyboard

        keyboard = create_time_keyboard(action="time", min_time="1200")

        assert isinstance(keyboard, InlineKeyboardMarkup)

        # Verify that times before min_time are filtered out
        all_buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("time;")
        ]

        for button in all_buttons:
            if ";" in button.callback_data:
                parts = button.callback_data.split(";")
                if len(parts) > 1 and parts[1].isdigit():
                    time_value = int(parts[1])
                    # Times should be >= 1200 (noon)
                    assert time_value >= 1200

    def test_handle_time_action_select(self):
        """Test time action selection"""
        from telegramBot.time_keyboard import handle_time_action

        callback_data = "time;0900"
        selected, time_result = handle_time_action(callback_data)

        assert selected is True
        assert time_result == "0900"

    def test_handle_time_action_reselect(self):
        """Test time reselect action"""
        from telegramBot.time_keyboard import handle_time_action

        callback_data = "reselect_time"
        selected, time_result = handle_time_action(callback_data)

        assert selected is True
        assert time_result == "reselect"

    def test_handle_time_action_ignore(self):
        """Test time ignore action"""
        from telegramBot.time_keyboard import handle_time_action

        callback_data = "time_ignore"
        selected, time_result = handle_time_action(callback_data)

        assert selected is False
        assert time_result is None

    def test_create_max_time_keyboard(self):
        """Test max time keyboard creation"""
        from telegramBot.time_keyboard import create_time_keyboard

        keyboard = create_time_keyboard(action="maxtime", min_time="0900")

        assert isinstance(keyboard, InlineKeyboardMarkup)

        # Verify callback data uses maxtime prefix
        all_buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("maxtime;")
        ]

        assert len(all_buttons) > 0
