"""로그에 비밀값이 남지 않게 함

텔레그램 봇 API 주소(https://api.telegram.org/bot<토큰>/...)에는 봇 토큰이 들어 있어서,
httpx 가 INFO 로 남기는 요청 로그나 오류 메시지(HTTPStatusError 는 주소를 포함)에 토큰이
그대로 찍힌다. 로그를 볼 수 있으면 봇을 조종할 수 있으므로 모든 로그에서 가린다.
"""

import logging
import re

# 텔레그램 봇 토큰: <봇 id>:<35자 안팎의 비밀값>
BOT_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
REDACTED = "<redacted-bot-token>"

# 요청 주소를 INFO 로 남기는 HTTP 라이브러리 (경고·오류만 남김)
QUIET_LOGGERS = ("httpx", "httpcore")


def redact(text: str) -> str:
    return BOT_TOKEN_RE.sub(REDACTED, text)


class RedactSecretsFilter(logging.Filter):
    """메시지와 예외 traceback 에서 봇 토큰을 가림 (핸들러에 붙여 모든 로거의 기록에 적용)"""

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_redaction() -> None:
    """지금 설정된 모든 로그 핸들러에 필터를 붙이고 HTTP 라이브러리 요청 로그를 끔"""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    redaction = RedactSecretsFilter()
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        for handler in logger.handlers:
            if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
                handler.addFilter(redaction)
