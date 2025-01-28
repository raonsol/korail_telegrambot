import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telegram import Update
from telegramBot.bot import TelegramBot

bot = TelegramBot(os.environ.get("BOTTOKEN"))


# webhook 등록 및 lifespan 설정
@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot.app.bot.set_webhook(f"{os.environ.get("WEBHOOK_URL")}/telebot/message")
    async with bot.app:
        await bot.app.start()
        yield
        await bot.app.stop()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(router)

# set environment variable for development
os.environ["IS_DEV"] = "true" if "dev" in sys.argv else "false"
print(f"Setting env as IS_DEV: {os.getenv('IS_DEV')}")


class Chat(BaseModel):
    id: int


class Message(BaseModel):
    text: str
    chat: Chat


class TelegramRequest(BaseModel):
    message: Message


@app.post("/telebot/message")
async def process_update(request: Request):
    req = await request.json()
    print("Request recieved", req)
    update = Update.de_json(req, bot.app.bot)
    await bot.app.process_update(update)
    return Response(status_code=status.HTTP_200_OK)


@app.post("/telebot/completion/{chatId}")
async def send_reservation_status(
    chatId: int, msg: str = Query(...), status: int = Query(...)
):
    """예약 프로세스에서 결과를 받아 사용자에게 메세지 전송

    Args:
        chatId (int): 텔레그램 채팅방 ID
        msg (str): 전송할 메시지
        status (int): 예약 상태 코드 (0이면 예약 완료)

    """
    if chatId not in bot.runningStatus:
        print(f"Chat ID {chatId} not found in running list.")
        return

    if status == 0:
        print("예약 완료, 상태 초기화")
        bot.reset_user_state(chatId)

    await bot.send_message(chatId, msg)
    del bot.runningStatus[chatId]
    # msgToSubscribers = f'{telebot_handler.userDict[chatId]["userInfo"]["korailId"]}의 예약이 종료되었습니다.'
    # telebot_handler.sendToSubscribers(msgToSubscribers)


if __name__ == "__main__":
    # Server will be run using the make run command, not inside app.py
    pass
