# 코레일 텔레그램 봇 - 테스트 스위트 문서

코레일 텔레그램 봇 예약 자동화 시스템을 위한 종합 테스트 스위트입니다.

## 목차

- [개요](#개요)
- [테스트 구조](#테스트-구조)
- [테스트 실행하기](#테스트-실행하기)
- [테스트 카테고리](#테스트-카테고리)
- [커버리지 리포트](#커버리지-리포트)
- [새로운 테스트 작성하기](#새로운-테스트-작성하기)
- [CI/CD 통합](#cicd-통합)

## 개요

이 테스트 스위트는 다음을 포괄적으로 커버합니다:
- **단위 테스트**: 개별 컴포넌트 테스트
- **통합 테스트**: 컴포넌트 간 상호작용 테스트
- **엔드투엔드 테스트**: 텔레그램 대화 전체 흐름과 여러 예약 동시 진행

테스트는 코레일·텔레그램 등 외부 서비스에 요청을 보내지 않습니다 (모두 가짜 객체로 대체).
실제 코레일 계정으로 확인할 일은 `make korail-login-check` 등 별도 스크립트로 하고,
`tests/` 아래 `test_*.py` 파일에는 넣지 않습니다 - pytest 가 파일을 불러오는 순간 모듈
최상단 코드가 실행되기 때문입니다.

### 테스트 통계

- **테스트 카테고리**: Unit, Integration, E2E
- **테스트된 실행 모드**: Subprocess와 Celery
- **커버리지 목표**: >80%

## 테스트 구조

```
tests/
├── conftest.py                     # 테스트 환경변수, 공유 픽스처 (services, fake_launcher ...)
├── unit/
│   ├── test_core_*.py              # core/ 도메인 (예약·러너·인증·사용자·출구·열차 조회·알림 ...)
│   ├── test_reservation_management.py  # 예약 후 관리 (코레일 취소, 결제 기한 알림, 인근역)
│   ├── test_korail_account.py      # 내 승차권 (웹 /tickets, 텔레그램 /mytickets)
│   ├── test_korail_client.py       # pykorail 래퍼 (로그인, 조회 페이지, 예약) + pykorail 계약
│   ├── test_korail_device.py       # 계정별 기기 신원(Android ID)
│   ├── test_waitlist.py            # 예약대기
│   ├── test_bot.py                 # 텔레그램 대화 상태·핸들러
│   ├── test_messages_keyboards.py  # 메시지·달력/시각 키보드
│   ├── test_web_api.py             # 웹앱 REST API (세션 쿠키, CSRF, 권한)
│   ├── test_web_push_delivery.py   # Web Push 실제 전송 (로컬 HTTP 서버)
│   ├── test_app.py / test_config.py / test_app_settings.py
│   ├── test_cache.py / test_log_redaction.py
│   └── test_task_id_keys.py        # 실제 Redis 필요 (없으면 건너뜀)
├── integration/
│   ├── test_subprocess_mode.py     # forkserver 워커, 결과 파일, 출구 파일
│   ├── test_celery_mode.py         # Celery 태스크 (fakeredis), 취소·재시작 감지
│   └── fake_workers.py             # 실제 워커 진입점에 넣는 가짜 코레일
├── e2e/
│   ├── test_reservation_flow.py    # /start 부터 예약 결과까지, 워커 비정상 종료
│   └── test_multiple_reservations.py  # 한 사용자의 여러 예약이 서로 영향 없음
└── README.md                       # 이 문서
```

## 테스트 실행하기

### 사전 준비사항

1. **의존성 설치**:
   ```bash
   make install
   ```

2. **테스트 의존성 설치**:
   ```bash
   uv sync
   ```

### 빠른 시작

```bash
# 모든 테스트 실행
make test

# 커버리지와 함께 실행
make test-coverage

# 특정 테스트 카테고리 실행
make test-unit
make test-integration
make test-e2e
```

### 상세한 테스트 실행

#### 모든 테스트 실행
```bash
uv run pytest
```

#### 특정 테스트 카테고리 실행

```bash
# 단위 테스트만
uv run pytest -m unit

# 통합 테스트만
uv run pytest -m integration

# E2E 테스트만
uv run pytest -m e2e

# Subprocess 모드 테스트
uv run pytest -m subprocess

# Celery 모드 테스트
uv run pytest -m celery
```

#### 특정 테스트 파일 실행

```bash
# 설정 테스트
uv run pytest tests/unit/test_config.py

# 봇 핸들러 테스트
uv run pytest tests/unit/test_bot.py

# 완전한 예약 플로우 테스트
uv run pytest tests/e2e/test_reservation_flow.py
```

#### 특정 테스트 함수 실행

```bash
# 특정 함수 테스트
uv run pytest tests/unit/test_config.py::TestWebSettings::test_web_settings_dev_mode

# 특정 클래스 테스트
uv run pytest tests/unit/test_bot.py::TestTelegramBot
```

### 테스트 실행 옵션

```bash
# 상세 출력
uv run pytest -v

# print 문 표시
uv run pytest -s

# 첫 실패 시 중단
uv run pytest -x

# 마지막 실패한 테스트만 실행
uv run pytest --lf

# 빠른 테스트만 실행 (느린 E2E 테스트 제외)
uv run pytest -m "not slow"
```

## 테스트 카테고리

### 단위 테스트 (`-m unit`)

개별 컴포넌트를 독립적으로 테스트합니다.

**파일**:
- 위 구조의 `unit/` 참고. `core/` 규칙(제한, 검증, 워커 보고)은 `test_core_*.py`,
  채널 어댑터(텔레그램, 웹)는 `test_bot.py`, `test_web_api.py` 에서 확인합니다

**예제**:
```bash
uv run pytest tests/unit/test_config.py -v
```

### 통합 테스트 (`-m integration`)

컴포넌트 간 상호작용 및 모드별 기능을 테스트합니다.

**파일**:
- `test_subprocess_mode.py`: Subprocess 실행 모드
- `test_celery_mode.py`: Celery 분산 실행 모드

**예제**:
```bash
uv run pytest tests/integration/ -v
```

**특별 요구사항**:
- 실제 Redis 가 필요한 테스트: `-m requires_redis` (없으면 건너뜀, CI MQ 단계에서 실행)

### 엔드투엔드 테스트 (`-m e2e`)

처음부터 끝까지 완전한 사용자 워크플로우를 테스트합니다.

**파일**:
- `test_reservation_flow.py`: /start 부터 예약 결과까지, 다른 사용자 대화, 워커 비정상 종료
- `test_multiple_reservations.py`: 예약마다 독립된 상태·콜백

**예제**:
```bash
# 모든 E2E 테스트 실행
uv run pytest tests/e2e/ -v

# 느린 E2E 테스트 제외
uv run pytest -m "e2e and not slow"
```

### 모드별 테스트

#### Subprocess 모드 테스트
```bash
uv run pytest -m subprocess
```

Subprocess 기반 백그라운드 태스크 실행을 테스트합니다.

#### Celery 모드 테스트
```bash
uv run pytest -m celery
```

Celery 기반 분산 태스크 실행을 테스트합니다 (Redis 필요).

## 커버리지 리포트

### 커버리지 리포트 생성

```bash
# HTML 커버리지 리포트 생성
uv run pytest --cov=src --cov-report=html

# 터미널에서 커버리지 보기
uv run pytest --cov=src --cov-report=term-missing

# XML 커버리지 생성 (CI/CD용)
uv run pytest --cov=src --cov-report=xml
```

### 커버리지 리포트 보기

```bash
# 브라우저에서 HTML 리포트 열기
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### 커버리지 목표

- **전체 커버리지**: >80%
- **핵심 모듈**: >90%
  - `config.py`
  - `bot.py`
  - `korail_client.py`
- **통합 테스트**: >70%
- **E2E 테스트**: 주요 경로 커버

## 새로운 테스트 작성하기

### 테스트 파일 이름 규칙

- 단위 테스트: `test_<모듈명>.py`
- 통합 테스트: `test_<기능>_mode.py`
- E2E 테스트: `test_<워크플로우>_flow.py`

### 테스트 함수 이름 규칙

```python
def test_<함수명>_<시나리오>():
    """테스트 설명"""
    pass
```

### 픽스처 사용하기

```python
async def test_my_function(services, valid_request):
    """conftest.py의 픽스처 사용 (임시 DB + 가짜 런처로 만든 Services)"""
    r = await services.reservations.start(owner, valid_request, "010", "pw", origin="web")
    assert r.is_active
```

### 테스트 마커 추가하기

```python
@pytest.mark.unit
def test_unit_functionality():
    """단위 테스트 예제"""
    pass

@pytest.mark.integration
@pytest.mark.requires_redis
def test_redis_integration():
    """Redis가 필요한 통합 테스트"""
    pass

@pytest.mark.e2e
@pytest.mark.slow
async def test_complete_flow():
    """E2E 테스트 예제"""
    pass
```

### 비동기 테스트 예제

```python
@pytest.mark.asyncio
async def test_async_function():
    """비동기 함수 테스트"""
    result = await some_async_function()
    assert result is not None
```

### 모킹 예제

```python
from unittest.mock import Mock, AsyncMock, patch

def test_with_mocks():
    """모킹을 사용한 테스트"""
    with patch('module.function') as mock_func:
        mock_func.return_value = "mocked"
        result = function_under_test()
        assert result == "mocked"
```

## 테스트 데이터 및 픽스처

### 사용 가능한 픽스처 (conftest.py에서)

- **`services`**: 임시 SQLite DB + 가짜 런처로 만든 `Services` (ALLOW_LIST 시드 포함)
- **`fake_launcher`**: 워커를 띄우지 않고 시작·취소 호출과 명세만 기록
- **`korail_login`**: 코레일 로그인 대체 (`"correct"`·관리자 비밀번호만 성공)
- **`test_settings`**: 테스트용 `WebSettings`
- **`valid_request`**: 7일 뒤 출발하는 올바른 `ReservationRequest`
- **`mock_telegram_update`**, **`mock_korail_client`**, **`mock_redis_client`** (fakeredis)
- **`sample_user_data`**, **`sample_train_data`**: 텔레그램 대화 상태·pykorail 열차 예시

### 커스텀 픽스처 만들기

`tests/conftest.py`에 추가:

```python
@pytest.fixture
def my_custom_fixture():
    """커스텀 픽스처"""
    # 설정
    data = {"key": "value"}
    yield data
    # 정리 (선택사항)
```

## CI/CD 통합

### GitHub Actions

테스트 스위트는 자동화된 테스트를 위해 GitHub Actions와 통합됩니다.

**설정 파일**: `.github/workflows/test.yml`

**트리거**:
- main 브랜치에 푸시
- 풀 리퀘스트
- 수동 워크플로우 실행

**테스트 매트릭스**:
- Python 3.13
- 여러 테스트 카테고리 (unit, integration, e2e)

### CI에서 테스트 실행

```bash
# CI 환경 시뮬레이션
IS_DEV=true \
BOTTOKEN_DEV=test_token \
WEBHOOK_URL_DEV=http://test.example.com \
ALLOW_LIST=01012345678 \
ADMINPW=test_admin \
uv run pytest
```

## 문제 해결

### 일반적인 문제

#### Import 에러

```bash
# PYTHONPATH에 src/ 포함 확인
export PYTHONPATH="${PYTHONPATH}:./src"
uv run pytest
```

또는 pytest.ini 설정 사용 (이미 설정됨).

#### Redis 연결 에러

```bash
# Celery 테스트 실행 전에 Redis 시작
make redis-start
uv run pytest -m celery
make redis-stop
```

#### 비동기 테스트 에러

`pytest-asyncio`가 설치되어 있는지 확인:
```bash
uv add --dev pytest-asyncio
```

#### 커버리지가 생성되지 않음

```bash
# coverage 패키지 설치
uv add --dev pytest-cov

# 커버리지와 함께 실행
uv run pytest --cov=src
```

### 테스트 환경

**필수 환경 변수**:
```bash
IS_DEV=true
BOTTOKEN_DEV=test_token
WEBHOOK_URL_DEV=http://test.example.com
ALLOW_LIST=01012345678
ADMINPW=test_password
```

이는 `tests/conftest.py` 맨 위에서 자동으로 설정됩니다 (`pythonpath = src` 등은 `pytest.ini`).

## 모범 사례

1. **테스트를 먼저 작성** (TDD 접근법)
2. **테스트를 독립적으로 유지** (테스트 간 의존성 없음)
3. **설명적인 이름 사용** (테스트 이름이 무엇을 테스트하는지 설명)
4. **외부 서비스 모킹** (Telegram API, 코레일 API)
5. **엣지 케이스 테스트** (잘못된 입력, 네트워크 에러 등)
6. **테스트 데이터 유지** (재사용 가능한 테스트 데이터에 픽스처 사용)
7. **테스트를 빠르게 유지** (느린 테스트에 `@pytest.mark.slow` 사용)
8. **복잡한 테스트 문서화** (무엇을 테스트하는지 설명하는 docstring 추가)

## 성능

### 테스트 실행 시간

- **단위 테스트**: ~5-10초
- **통합 테스트**: ~15-30초
- **E2E 테스트**: ~30-60초
- **전체 스위트**: ~1-2분

### 테스트 속도 향상

```bash
# 느린 테스트(E2E) 건너뛰기
uv run pytest -m "not slow"

# 커버리지 없이 (빠름)
uv run pytest --no-cov
```

## 유지보수

### 테스트 업데이트

코드 업데이트 시:
1. 해당 테스트 업데이트
2. 관련 테스트 카테고리 실행
3. 커버리지가 감소하지 않는지 확인
4. 필요시 테스트 문서 업데이트

### 테스트 폐기

기능 제거 시:
1. 테스트를 deprecated로 표시
2. 문서 업데이트
3. 다음 버전에서 제거

## 지원

문제나 질문이 있는 경우:
1. 이 문서 확인
2. 테스트 파일의 테스트 예제 검토
3. 실패에 대한 CI/CD 로그 확인
4. GitHub에 이슈 열기

## 라이선스

메인 프로젝트와 동일한 라이선스.
