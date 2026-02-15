# 테스트 가이드 - 빠른 참조

코레일 텔레그램 봇을 위한 완전한 테스트 문서입니다.

## 빠른 시작

```bash
# 의존성 설치
make install

# 모든 테스트 실행
make test

# 커버리지와 함께 실행
make test-coverage
```

## 테스트 커맨드

### 기본 커맨드

```bash
make test                  # 모든 테스트 실행
make test-unit            # 단위 테스트만
make test-integration     # 통합 테스트만
make test-e2e            # 엔드투엔드 테스트만
make test-coverage       # 커버리지 리포트와 함께 실행
make test-fast           # 느린 테스트 건너뛰기
make coverage-html       # HTML 커버리지 리포트 생성 및 열기
```

### 고급 커맨드

```bash
# 특정 테스트 카테고리 실행
make test-subprocess     # Subprocess 모드 테스트
make test-celery        # Celery 모드 테스트 (Redis 필요)

# 직접 pytest 커맨드
pipenv run pytest                           # 모든 테스트
pipenv run pytest -m unit                  # 단위 테스트
pipenv run pytest -m "not slow"           # 빠른 테스트만
pipenv run pytest tests/unit/test_bot.py  # 특정 파일
pipenv run pytest -v -s                   # 상세 출력과 함께
pipenv run pytest -x                      # 첫 실패 시 중단
pipenv run pytest --lf                    # 마지막 실패한 테스트만
```

## 테스트 구조

```
tests/
├── unit/                   # 단위 테스트 (독립된 컴포넌트)
│   ├── test_config.py     # 설정 테스트
│   ├── test_bot.py        # 봇 핸들러 테스트
│   ├── test_korail_client.py  # API 클라이언트 테스트
│   ├── test_messages_keyboards.py  # UI 테스트
│   └── test_app.py        # FastAPI 엔드포인트 테스트
├── integration/           # 통합 테스트 (컴포넌트 상호작용)
│   ├── test_subprocess_mode.py  # Subprocess 모드 테스트
│   ├── test_celery_mode.py      # Celery 모드 테스트
│   └── test_make_commands.py    # 빌드 시스템 테스트
└── e2e/                   # 엔드투엔드 테스트 (완전한 워크플로우)
    └── test_reservation_flow.py  # 완전한 예약 플로우
```

## 커버리지 목표

- **전체**: >80%
- **핵심 모듈** (config, bot, korail_client): >90%
- **통합 테스트**: >70%
- **E2E 테스트**: 주요 경로 커버

## 테스트 카테고리 (마커)

```bash
-m unit              # 단위 테스트
-m integration       # 통합 테스트
-m e2e              # 엔드투엔드 테스트
-m subprocess       # Subprocess 모드 테스트
-m celery           # Celery 모드 테스트 (Redis 필요)
-m slow             # 느린 테스트
-m requires_redis   # Redis가 필요한 테스트
-m requires_external # 외부 서비스가 필요한 테스트
```

## 특정 테스트 실행하기

```bash
# 마커로
pipenv run pytest -m unit
pipenv run pytest -m "integration and not requires_redis"

# 파일로
pipenv run pytest tests/unit/test_config.py

# 클래스로
pipenv run pytest tests/unit/test_bot.py::TestTelegramBot

# 함수로
pipenv run pytest tests/unit/test_config.py::TestWebSettings::test_web_settings_dev_mode
```

## 다양한 테스트 타입을 위한 사전 준비사항

### 단위 테스트
- 외부 의존성 없음
- 언제 어디서나 실행 가능

### 통합 테스트 (Subprocess)
- 외부 의존성 없음
- Subprocess 기반 실행 테스트

### 통합 테스트 (Celery)
- **필요**: Redis 실행 중
- Redis 시작: `make redis-start`
- Redis 중지: `make redis-stop`

### E2E 테스트
- 실행 시간이 더 걸릴 수 있음
- `@pytest.mark.slow`로 표시

## 일반적인 테스트 시나리오

### 설정 로딩 테스트

```bash
# 개발 모드 테스트
pipenv run pytest tests/unit/test_config.py::TestWebSettings::test_web_settings_dev_mode

# 프로덕션 모드 테스트
pipenv run pytest tests/unit/test_config.py::TestWebSettings::test_web_settings_production_mode
```

### 봇 핸들러 테스트

```bash
# start 커맨드 테스트
pipenv run pytest tests/unit/test_bot.py::TestTelegramBot::test_start_func

# 사용자 입력 검증 테스트
pipenv run pytest tests/unit/test_bot.py::TestTelegramBot::test_input_id_valid_phone
```

### 예약 플로우 테스트

```bash
# 완전한 플로우
pipenv run pytest tests/e2e/test_reservation_flow.py::TestCompleteReservationFlow::test_full_reservation_flow_subprocess_mode

# 에러 처리
pipenv run pytest tests/e2e/test_reservation_flow.py::TestErrorRecovery
```

### API 클라이언트 테스트

```bash
# 로그인 테스트
pipenv run pytest tests/unit/test_korail_client.py::TestReserveHandler::test_login_success

# 예약 테스트
pipenv run pytest tests/unit/test_korail_client.py::TestReserveHandler::test_reserve_single_attempt_success
```

## 테스트를 위한 환경 변수

테스트는 자동으로 이 값들을 사용합니다 (`pytest.ini`에 설정됨):

```bash
IS_DEV=true
BOTTOKEN_DEV=test_bot_token_dev
BOTTOKEN=test_bot_token_prod
WEBHOOK_URL_DEV=http://test-dev.example.com
WEBHOOK_URL=http://test-prod.example.com
ALLOW_LIST=01012345678,01087654321
ADMINPW=test_admin_password
ADMIN_KORAIL_ID=admin_user
ADMIN_KORAIL_PW=admin_pass
```

필요시 오버라이드:
```bash
IS_DEV=false pipenv run pytest
```

## CI/CD 통합

### GitHub Actions 워크플로우

파일: `.github/workflows/test.yml`

**실행 조건**:
- main/develop 브랜치에 푸시
- 풀 리퀘스트
- 수동 트리거

**작업**:
1. **Lint**: 코드 포맷팅 체크
2. **단위 테스트**: 빠른 독립 테스트
3. **통합 (Subprocess)**: Subprocess 모드 테스트
4. **통합 (Celery)**: Redis와 함께 Celery 모드 테스트
5. **E2E 테스트**: 완전한 워크플로우 테스트
6. **커버리지 리포트**: 통합 커버리지 분석
7. **Docker 빌드**: 이미지 빌드 검증

### CI 결과 보기

1. GitHub Actions 탭으로 이동
2. 최신 워크플로우 실행 클릭
3. 작업 결과 및 로그 보기
4. 커버리지 아티팩트 다운로드

## 커버리지 리포트

### 커버리지 생성

```bash
# 터미널 리포트
make test-coverage

# HTML 리포트 (브라우저에서 자동 열림)
make coverage-html

# HTML 리포트 수동으로 보기
open htmlcov/index.html
```

### 커버리지 이해하기

- **녹색**: >90% 커버리지
- **노란색**: 70-90% 커버리지
- **빨간색**: <70% 커버리지

**중점 영역**:
- 주요 경로가 커버되었는지 확인
- 핵심 비즈니스 로직 우선
- 100%에 집착하지 말기 (수익 감소)

## 새로운 테스트 작성하기

### 1. 테스트 타입 선택

- **Unit**: 단일 함수/클래스 테스트
- **Integration**: 컴포넌트 상호작용 테스트
- **E2E**: 완전한 사용자 워크플로우 테스트

### 2. 테스트 파일 생성

```python
# tests/unit/test_myfeature.py
import pytest
from unittest.mock import Mock, AsyncMock

@pytest.mark.unit
class TestMyFeature:
    """내 기능 테스트"""

    def test_basic_functionality(self):
        """기본 기능 테스트"""
        result = my_function()
        assert result == expected_value

    @pytest.mark.asyncio
    async def test_async_functionality(self):
        """비동기 기능 테스트"""
        result = await my_async_function()
        assert result is not None
```

### 3. 픽스처 사용

```python
def test_with_fixtures(mock_telegram_bot, sample_user_data):
    """미리 정의된 픽스처 사용"""
    # 픽스처가 자동으로 주입됨
    assert mock_telegram_bot is not None
```

### 4. 마커 추가

```python
@pytest.mark.integration
@pytest.mark.requires_redis
def test_redis_functionality():
    """Redis가 필요한 통합 테스트"""
    pass
```

## 문제 해결

### Import 에러

```bash
# PYTHONPATH에 src 추가 (pytest.ini에 이미 설정됨)
export PYTHONPATH="${PYTHONPATH}:./src"
```

### Redis 연결 에러

```bash
# Redis 시작
make redis-start

# Redis 실행 확인
redis-cli ping

# 완료 후 Redis 중지
make redis-stop
```

### 비동기 테스트 에러

```bash
# pytest-asyncio 설치 확인
pipenv install --dev pytest-asyncio
```

### 커버리지가 생성되지 않음

```bash
# pytest-cov 설치
pipenv install --dev pytest-cov

# 커버리지 플래그와 함께 실행
pipenv run pytest --cov=src
```

### 테스트 실행이 느림

```bash
# 병렬로 실행 (pytest-xdist 필요)
pipenv install --dev pytest-xdist
pipenv run pytest -n auto

# 느린 테스트 건너뛰기
pipenv run pytest -m "not slow"
```

## 모범 사례

1. **테스트를 먼저 작성** (TDD)
2. **테스트를 독립적으로 유지** (테스트 간 의존성 없음)
3. **설명적인 이름 사용** (`test_user_login_with_valid_credentials`)
4. **외부 서비스 모킹** (Telegram, 코레일 API)
5. **엣지 케이스 테스트** (잘못된 입력, 네트워크 에러)
6. **테스트를 빠르게 유지** (비용이 많이 드는 작업 모킹)
7. **테스트당 하나의 assertion** (가능한 경우)
8. **복잡한 테스트 문서화** (docstring 추가)

## 성능 벤치마크

| 테스트 카테고리 | 예상 시간 |
|--------------|----------|
| 단위 테스트 | ~5-10초 |
| 통합 테스트 | ~15-30초 |
| E2E 테스트 | ~30-60초 |
| 전체 스위트 | ~1-2분 |

## 유용한 리소스

- **전체 문서**: `tests/README.ko.md`
- **픽스처**: `tests/conftest.py`
- **테스트 설정**: `pytest.ini`
- **CI 설정**: `.github/workflows/test.yml`
- **커버리지 리포트**: `htmlcov/index.html` (테스트 실행 후)

## 지원

문제나 질문이 있는 경우:
1. `tests/README.ko.md`에서 상세 문서 확인
2. 기존 테스트 파일의 테스트 예제 검토
3. GitHub Actions의 CI/CD 로그 확인
4. GitHub에 이슈 열기

## 빠른 팁

```bash
# 패턴에 맞는 테스트 실행
pipenv run pytest -k "test_login"

# print 문과 함께 테스트 실행
pipenv run pytest -s

# 추가 상세 정보와 함께
pipenv run pytest -vv

# 실패 시 디버거로 진입
pipenv run pytest --pdb

# 테스트 리포트 생성
pipenv run pytest --html=report.html

# 테스트 소요 시간 확인
pipenv run pytest --durations=10
```

---

**기억하세요**: 좋은 테스트는:
- **빠름** (빠르게 실행)
- **독립적** (공유 상태 없음)
- **반복 가능** (매번 같은 결과)
- **자체 검증** (합격/불합격, 수동 확인 없음)
- **적시** (코드와 함께 또는 이전에 작성)
