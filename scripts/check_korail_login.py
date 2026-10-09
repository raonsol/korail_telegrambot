"""관리자 계정(ADMIN_KORAIL_ID/PW)으로 코레일 로그인을 진단

컨테이너가 실제로 받은 ID/PW 상태(비밀번호 자체는 출력하지 않음)와 코레일 서버가 준 실패 코드·사유를 보여줌.
예약과 같은 출구(KORAIL_EGRESSES 중 이 계정에 고정된 출구)로 요청한다.
사용: make korail-login-check  (Docker 컨테이너 안에서 실행됨)
주의: 실행할 때마다 로그인 1회로 집계됨 (비밀번호 5회 연속 오류 시 로그인 제한)
"""

import os
from pykorail import AccessRestrictedError, HttpStatusError, KorailError
from config import web_settings
from core.egress import EgressPool
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


def admin_device(korail_id):
    """웹/텔레그램 관리자 로그인과 같은 기기 신원 (users 테이블의 관리자 코레일 계정 행)"""
    try:
        from core.db import Database
        from core.users import UserService

        users = UserService(Database(web_settings.database_url), admin_korail_id=kid)
        return users.korail_device(korail_id)
    except Exception as e:
        print(f"⚠️  기기 신원을 불러오지 못해 새 Android ID로 접속합니다: {e}")
        return None


egress = EgressPool.from_settings(web_settings).for_account(kid)
print(f"출구 : {egress.id} ({'직접 연결' if egress.is_direct else '프록시'})")
device = admin_device(kid)
print(
    f"기기 : {device['profile_id'] if device else '-'} / Android ID {device['android_id'] if device else '(새로 생성)'}"
)

client = create_korail_client(egress.proxy_url, device=device)
try:
    client.login(kid, kpw)
    print("✅ 로그인 성공")
except AccessRestrictedError as e:
    print(
        f"⛔ 코레일 서버 차단 응답 (출구 {egress.id}): HTTP {e.status_code}, code={e.code}, {e.msg}"
    )
except HttpStatusError as e:
    # 코레일 API 형식이 아닌 HTTP 4xx·5xx (프록시·앞단 거절 등) - 본문 앞부분이 msg 로 옴
    print(
        f"❌ 코레일 서버가 요청을 거절함: HTTP {e.status_code}, code={e.code}, {e.msg}"
    )
except KorailError as e:
    # 코레일 API 응답의 실패 사유 (h_msg_cd / h_msg_txt). code 가 None 이면 서버가 사유를 주지 않음
    print(f"❌ 로그인 실패: h_msg_cd={e.code}, h_msg_txt={e.msg}")
except Exception as e:
    print(f"❌ 로그인 실패 ({type(e).__name__}): {e}")
finally:
    client.close()
