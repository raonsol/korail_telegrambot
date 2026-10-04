"""관리자 계정(ADMIN_KORAIL_ID/PW)으로 코레일 로그인을 진단

컨테이너가 실제로 받은 ID/PW 상태(비밀번호 자체는 출력하지 않음)와 코레일 서버의 원본 응답을 보여줌.
사용: make korail-login-check  (Docker 컨테이너 안에서 실행됨)
주의: 실행할 때마다 로그인 1회로 집계됨 (비밀번호 5회 연속 오류 시 로그인 제한)
"""

import json
import os
from telegramBot.korail_client import create_korail_client

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
KORAIL_KEYS = ("strResult", "h_msg_cd", "h_msg_txt")

client = create_korail_client()
responses = []
# 서버 응답을 가로채 기록 (pykorail 은 실패 시 원본 응답을 버리고, HTTP 4xx·5xx 는 파싱 중 예외를 냄)
parse = client._api._parse
client._api._parse = lambda resp: responses.append(resp) or parse(resp)
try:
    client.login(kid, kpw)
    print("✅ 로그인 성공")
except Exception as e:
    print(f"❌ 로그인 실패: {e}")
    last = responses[-1] if responses else None
    try:
        p = json.loads(last.text) if last is not None else {}
    except ValueError:
        p = {}
    if not isinstance(p, dict):
        p = {}
    print("HTTP 상태:", getattr(last, "status_code", None))
    if any(k in p for k in KORAIL_KEYS):
        print("서버 응답:", {k: p.get(k) for k in KORAIL_KEYS})
    else:
        # 코레일 API 형식이 아님 (서버 차단 code -2000 등) - 개인정보가 없으므로 전체 출력
        print(
            "⚠️  코레일 API 형식이 아닌 응답:", p or (last.text[:200] if last else None)
        )
finally:
    client.close()
