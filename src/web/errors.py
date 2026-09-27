"""API 오류 응답 형식 통일: {"code": ..., "message": ...}"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.errors import ServiceError


def _validation_message(exc: RequestValidationError) -> str:
    for error in exc.errors():
        message = str(error.get("msg", ""))
        if message.startswith("Value error, "):
            return message[len("Value error, ") :]
        location = ".".join(str(p) for p in error.get("loc", ())[1:])
        return f"{location}: {message}" if location else message
    return "요청 형식이 올바르지 않습니다."


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION", "message": _validation_message(exc)},
        )
