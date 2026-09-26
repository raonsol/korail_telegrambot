"""코레일 비밀번호 암·복호화"""

import base64
import hashlib
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


class CredentialVault:
    """Fernet 기반 암호화.

    ``WEBAPP_ENC_KEY``에 임의의 문자열을 설정하면 SHA-256으로 Fernet 키를 유도합니다.
    설정하지 않으면 프로세스마다 임시 키를 생성하므로(재시작 시 웹 세션 만료),
    운영 환경에서는 반드시 설정해야 합니다.
    """

    def __init__(self, secret: str = ""):
        self.persistent = bool(secret)
        if secret:
            key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        else:
            logger.warning(
                "WEBAPP_ENC_KEY is not set; using an ephemeral key "
                "(web sessions will expire on restart)"
            )
            key = Fernet.generate_key()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None
