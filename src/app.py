import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telegram import Update
from telegramBot.bot import TelegramBot
from telegramBot.messages import Messages


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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

if not bot_token:
    logger.error("Bot token not found in environment variables")
    raise ValueError("Bot token is required")

logger.info(f"Using bot token: {bot_token[:10]}...")
bot = TelegramBot(bot_token)


# webhook 등록 및 lifespan 설정
@asynccontextmanager
async def lifespan(_: FastAPI):
    url = (
        os.environ.get("WEBHOOK_URL_DEV")
        if os.environ.get("IS_DEV") == "true"
        else os.environ.get("WEBHOOK_URL")
    )

    if not url:
        logger.error("Webhook URL not found in environment variables")
        raise ValueError("Webhook URL is required")

    webhook_url = f"{url}/message"
    logger.info(f"Setting webhook to {webhook_url}")

    try:
        # webhook 등록 시도
        result = await bot.set_webhook(url=webhook_url)
        if result:
            logger.info("Webhook set successfully")
        else:
            logger.error("Failed to set webhook")

        # 현재 webhook 정보 확인
        webhook_info = await bot.app.bot.get_webhook_info()
        logger.info(f"Current webhook info: {webhook_info}")

    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        # webhook 설정 실패해도 서버는 계속 실행되도록 함

    async with bot.app:
        await bot.app.start()
        logger.info("Bot application started")
        yield
        logger.info("Shutting down bot application")
        await bot.app.stop()


# 서버 시작
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "korail_telegrambot"}


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
