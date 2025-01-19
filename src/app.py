import os
import sys
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

app.include_router(router)

# set environment variable for development
os.environ["IS_DEV"] = "true" if "dev" in sys.argv else "false"
print(f"Setting env as IS_DEV: {os.getenv('IS_DEV')}")

if __name__ == "__main__":
    # Server will be run using the make run command, not inside app.py
    pass
