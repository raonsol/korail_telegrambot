from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from telegramBot.telebotApiHandler import router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/telebot")

if __name__ == "__main__":
    # Server will be run using the make run command
    pass
