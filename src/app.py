import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telegram import Update
from telegramBot.bot import TelegramBot
from telegramBot.messages import Messages


# set environment variable for development
os.environ["IS_DEV"] = "true" if "dev" in sys.argv else "false"
print(
    f"Setting env as {'development' if os.environ.get('IS_DEV')=="true" else 'production'}"
)

bot_token = (
    os.environ.get("BOTTOKEN_DEV")
    if os.environ.get("IS_DEV") == "true"
    else os.environ.get("BOTTOKEN")
)
bot = TelegramBot(bot_token)


# webhook 등록 및 lifespan 설정
@asynccontextmanager
async def lifespan(_: FastAPI):
    url = (
        os.environ.get("WEBHOOK_URL_DEV")
        if os.environ.get("IS_DEV") == "true"
        else os.environ.get("WEBHOOK_URL")
    )
    print(f"Setting webhook to {url}")
    await bot.set_webhook(url=f"{url}/message")
    async with bot.app:
        await bot.app.start()
        yield
        await bot.app.stop()


# 서버 시작
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Chat(BaseModel):
    id: int


class Message(BaseModel):
    text: str
    chat: Chat


class TelegramRequest(BaseModel):
    message: Message


@app.post("/message")
async def process_update(request: Request):
    req = await request.json()
    print("Request recieved", req)
    update = Update.de_json(req, bot.app.bot)
    # await bot.app.process_update(update)
    await bot.app.update_queue.put(update)
    return Response(status_code=status.HTTP_200_OK)


@app.post("/completion/{chat_id}")
async def send_reservation_status(
    chat_id: int, status: int = Query(...), reserveInfo: str = Query(...)
):
    """예약 프로세스에서 결과를 받아 사용자에게 메시지 전송

    Args:
        chat_id (int): 텔레그램 채팅방 ID
        status (int): 예약 상태 코드
            1: 예약 성공
            0: 예약 실패
            -1: 예약 오류
        reserveInfo (str): 예약 정보 문자열
    """
    if chat_id not in bot.runningStatus:
        print(f"Chat ID {chat_id}는 예약 큐에 없습니다")
        return

    # Handle messages based on status code
    if status == 1:
        msg = Messages.Info.RESERVE_SUCCESS.format(reserveInfo=reserveInfo)
    elif status == -1:
        msg = Messages.Error.RESERVE_WRONG
    else:
        msg = Messages.Error.RESERVE_FAILED

    await bot.send_message(chat_id, msg)

    # Reset user state if reservation process is complete
    if status == 1:
        print("예약 완료, 상태 초기화")
        bot._reset_user_state(chat_id)

    del bot.runningStatus[chat_id]
    # msgToSubscribers = f'{telebot_handler.userDict[chatId]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
    # telebot_handler.sendToSubscribers(msgToSubscribers)


if __name__ == "__main__":
    # Server will be run using the make run command, not inside app.py
    pass
