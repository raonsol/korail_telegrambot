"""Web Push용 VAPID 키 생성

    make vapid-keys   (또는 cd src && python -m core.vapid)

출력된 값을 .env의 VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY에 설정합니다.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate_vapid_keys() -> tuple[str, str]:
    """(public_key, private_key)

    - public_key: 브라우저 ``applicationServerKey`` 형식 (uncompressed point, base64url)
    - private_key: pywebpush가 읽는 raw 32바이트 (base64url)
    """
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(public_raw), _b64url(private_raw)


if __name__ == "__main__":
    public_key, private_key = generate_vapid_keys()
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
