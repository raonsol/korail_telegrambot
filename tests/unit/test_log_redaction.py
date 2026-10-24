"""로그에서 텔레그램 봇 토큰 가리기"""

import io
import logging

import pytest

from core.log_redaction import (
    REDACTED,
    RedactSecretsFilter,
    install_log_redaction,
    redact,
)

TOKEN = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_0123456789-ab"  # 가짜 토큰
URL = f"https://api.telegram.org/bot{TOKEN}/setWebhook"


@pytest.fixture
def capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactSecretsFilter())
    logger = logging.getLogger("test.redaction")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield logger, stream
    logger.removeHandler(handler)


def test_redact_keeps_other_text():
    assert redact(f'POST {URL} "HTTP/1.1 200 OK"') == (
        f'POST https://api.telegram.org/bot{REDACTED}/setWebhook "HTTP/1.1 200 OK"'
    )
    # 전화번호·시각·예약 id 같은 일반 값은 그대로
    text = "010-1234-5678 12:30 reservation 3f2a9c 01012345678"
    assert redact(text) == text


def test_redacts_message_args(capture):
    logger, stream = capture
    logger.info('HTTP Request: POST %s "%s"', URL, "HTTP/1.1 200 OK")
    output = stream.getvalue()
    assert TOKEN not in output
    assert REDACTED in output and "HTTP/1.1 200 OK" in output


def test_redacts_exception_traceback(capture):
    logger, stream = capture
    try:
        raise RuntimeError(f"Client error '404 Not Found' for url '{URL}'")
    except RuntimeError:
        logger.exception("send failed")
    output = stream.getvalue()
    assert TOKEN not in output
    assert "RuntimeError" in output and REDACTED in output


def test_install_quiets_http_libraries_and_filters_root_handlers():
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    levels = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    try:
        install_log_redaction()
        install_log_redaction()  # 두 번 불러도 필터는 하나
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert sum(isinstance(f, RedactSecretsFilter) for f in handler.filters) == 1
    finally:
        root.removeHandler(handler)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
