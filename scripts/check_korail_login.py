"""관리자 계정(ADMIN_KORAIL_ID/PW)으로 코레일 로그인을 진단

컨테이너가 실제로 받은 ID/PW 상태(비밀번호 자체는 출력하지 않음)와 코레일 서버의 원본 응답을 보여줌.
사용: make korail-login-check  (Docker 컨테이너 안에서 실행됨)
주의: 실행할 때마다 로그인 1회로 집계됨 (비밀번호 5회 연속 오류 시 로그인 제한)
"""

import os
from telegramBot.korail_client import create_korail_client, get_warp_proxy_url

kid = os.environ.get("ADMIN_KORAIL_ID", "")
kpw = os.environ.get("ADMIN_KORAIL_PW", "")
mask = lambda v: (
    v[:2] + "*" * max(len(v) - 4, 0) + v[-2:] if len(v) > 4 else "*" * len(v)
)
print(f"ID : {mask(kid)} (길이 {len(kid)}, 앞뒤 공백 {kid != kid.strip()})")
print(
    f"PW : 길이 {len(kpw)} | 앞뒤 공백 {kpw != kpw.strip()} | 따옴표 {any(c in kpw for c in chr(34) + chr(39))}"
    f" | $ {'$' in kpw} | # {'#' in kpw} | \\r {chr(13) in kpw}"
)
print(f"WARP: {get_warp_proxy_url() or '사용 안 함'}")

client = create_korail_client()
payloads = []
parse = (
    client._api._parse
)  # 서버 응답을 가로채 기록 (pykorail 은 실패 시 원본 응답을 버림)
client._api._parse = lambda resp: payloads.append(parse(resp)) or payloads[-1]
try:
    client.login(kid, kpw)
    print("✅ 로그인 성공")
except Exception as e:
    print(f"❌ 로그인 실패: {e}")
    p = payloads[-1] if payloads else {}
    print(
        "서버 응답:",
        {k: p.get(k) for k in ("strResult", "h_msg_cd", "h_msg_txt")},
        "| 응답 키:",
        sorted(p),
    )
finally:
    client.close()
