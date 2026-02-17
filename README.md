# KTX 예약 텔레그램 봇

이중 실행 모드와 포괄적인 Docker 지원을 갖춘 한국철도(KTX) 자동 예약 텔레그램 봇입니다.

## 특징

- 🚄 **자동 KTX 예약**: 재시도 로직을 통한 자동 기차표 예약
- 🔄 **이중 실행 모드**: 경량 subprocess 모드 또는 확장 가능한 MQ 모드 선택
- 📱 **대화형 인터페이스**: 캘린더 기반 날짜 선택 및 시간 선호도 설정
- 🐳 **Docker 지원**: 개발 및 운영 환경을 위한 완전한 컨테이너화
- 🔐 **인증 시스템**: 전화번호 인증 및 안전한 사용자 관리
- 📊 **실시간 업데이트**: 웹훅 기반 상태 알림

## 아키텍처 개요

### 실행 모드

#### Subprocess 모드 (경량)
- **사용 사례**: 단일 사용자 또는 소규모 배포
- **저장소**: 메모리 내 데이터 저장 (Python 딕셔너리)
- **의존성**: 최소 - FastAPI 웹 서버만 필요
- **백그라운드 작업**: Python subprocess 실행
- **리소스 사용량**: 낮은 메모리 및 CPU 사용량

#### MQ 모드(Celery를 사용한 message queue 방식, 확장 가능)
- **사용 사례**: 다중 사용자 또는 대용량 배포
- **저장소**: 상태 관리 및 작업 큐를 위한 Redis
- **의존성**: Redis, PostgreSQL, Celery workers
- **백그라운드 작업**: 분산 작업 처리
- **리소스 사용량**: 높지만 수평 확장 가능

### 환경 구성

#### 개발 모드
- **포트**: 8390
- **기능**: 핫 리로드, 소스 코드 마운팅, 별도 개발 데이터베이스
- **웹훅**: `WEBHOOK_URL_DEV` 및 `BOTTOKEN_DEV` 사용

#### 운영 모드
- **포트**: 8391
- **기능**: 성능 및 안정성 최적화
- **웹훅**: `WEBHOOK_URL` 및 `BOTTOKEN` 사용

## 빠른 시작

### 사전 요구사항

- Python 3.13+
- Docker 및 Docker Compose
- 텔레그램 봇 토큰
- 환경 변수 설정

### 환경 변수

프로젝트 루트에 `.env` 파일을 생성하세요:

```env
# 봇 설정
BOTTOKEN=운영용_텔레그램_봇_토큰
BOTTOKEN_DEV=개발용_텔레그램_봇_토큰
WEBHOOK_URL=https://your-domain.com/telebot
WEBHOOK_URL_DEV=https://your-domain.com/telebot_dev

# 코레일 계정 (관리자 모드용)
USERID=코레일_사용자명
USERPW=코레일_비밀번호

# 보안
ALLOW_LIST=전화번호1,전화번호2,전화번호3
ADMINPW=관리자_비밀번호

# Message Queue(MQ) 모드 (선택사항)
REDIS_URL=redis://localhost:6379
CELERY_BROKER=redis://localhost:6379
CELERY_RESULT_BACKEND=redis://localhost:6379
```

### 설치

#### 로컬 개발

```bash
# 의존성 설치
make install

# 개발 모드 실행 (subprocess)
make dev

# 개발 모드 실행 (MQ)
make dev-mq

# 운영 모드 실행 (subprocess)
make run

# 운영 모드 실행 (MQ)
make run-mq
```

#### Docker 개발

```bash
# Docker 이미지 빌드
make docker-build

# 개발 - Subprocess 모드
make docker-compose-up

# 개발 - MQ 모드
make docker-compose-up-mq

# 모든 개발 컨테이너 중지
make docker-compose-down
```

#### Docker 운영

```bash
# 운영 - Subprocess 모드
make docker-compose-up

# 운영 - Celery 모드(MQ 방식)
make docker-compose-up-mq

# 모든 운영 컨테이너 중지
make docker-compose-down
```

## Make 명령어 참조

### 개발 명령어

| 명령어 | 설명 |
|--------|------|
| `make install` | pipenv로 의존성 설치 |
| `make dev` | 개발 서버 실행 (subprocess 모드, 포트 8390) |
| `make dev-mq` | 개발 서버 실행 (MQ 방식, 포트 8390) |
| `make run` | 운영 서버 실행 (subprocess 모드, 포트 8391) |
| `make run-mq` | 운영 서버 실행 (MQ 방식, 포트 8391) |

### Celery 명령어

| 명령어 | 설명 |
|--------|------|
| `make celery-worker-start` | Celery 워커 시작 |
| `make celery-worker-stop` | Celery 워커 중지 |
| `make celery-flower-start` | Flower 모니터링 시작 |
| `make celery-flower-stop` | Flower 모니터링 중지 |

### Docker 명령어

| 명령어 | 설명 |
|--------|------|
| `make docker-build` | Docker 이미지 빌드 |
| `make docker-push` | Docker 이미지를 레지스트리에 푸시 |

### Docker Compose 명령어

| 명령어 | 설명 |
|--------|------|
| `make docker-compose-up` | 운영 서비스 시작 (subprocess 모드) |
| `make docker-compose-up-mq` | 운영 서비스 시작 (MQ 방식) |
| `make docker-compose-down` | 모든 운영 서비스 중지 |

### 코드 품질

| 명령어 | 설명 |
|--------|------|
| `make lint` | black으로 코드 포맷팅 |

## 모드 선택 가이드

### Subprocess 모드를 사용해야 할 때
- ✅ 단일 사용자 또는 소규모 팀 사용
- ✅ 간단한 배포 요구사항
- ✅ 제한된 서버 리소스
- ✅ 빠른 프로토타이핑 또는 테스트

### MQ 모드를 사용해야 할 때
- ✅ 다중 동시 사용자
- ✅ 대용량 예약 요청
- ✅ 작업 모니터링 및 관리 필요
- ✅ 확장 가능한 운영 환경

## API 엔드포인트

### 상태 확인
```
GET /health
```

### 웹훅 엔드포인트
```
POST /message
```

### 예약 완료 (Subprocess 모드)
```
POST /completion/{chat_id}?status={status}&reserveInfo={info}
```

### 예약 콜백 (MQ 모드)
```
POST /reservation_callback
```

## 봇 명령어

- `/start` - 봇 초기화 및 인증 시작
- `/help` - 도움말 메시지 표시
- `/cancel` - 현재 예약 프로세스 취소
- `/status` - 현재 예약 상태 확인

## 프로젝트 구조

```
korail_telegrambot/
├── src/
│   ├── app.py                 # FastAPI 애플리케이션 진입점
│   ├── config.py             # 설정 관리
│   └── telegramBot/
│       ├── bot.py            # 메인 봇 로직
│       ├── tasks.py          # Celery 작업
│       ├── worker.py         # Subprocess 워커
│       ├── korail_client.py  # 코레일 API 클라이언트
│       ├── messages.py       # 메시지 템플릿
│       └── keyboards/        # 키보드 인터페이스
├── docker-compose.yml        # 운영 Docker 설정
├── docker-compose.dev.yml    # 개발 Docker 오버라이드
├── Dockerfile               # 컨테이너 빌드 지시사항
├── Makefile                 # 빌드 및 실행 명령어
├── Pipfile                  # Python 의존성
├── CLAUDE.md               # AI 어시스턴트 지시사항
└── README.md               # 이 파일
```

## 상세 설정 가이드

### 텔레그램 봇 설정

1. **BotFather를 통한 봇 생성**
   - 텔레그램에서 @BotFather에게 `/newbot` 명령어 전송
   - 봇 이름과 사용자명 설정
   - 발급받은 토큰을 `.env` 파일의 `BOTTOKEN`에 설정

2. **웹훅 설정**
   ```bash
   curl -F "url=https://your-domain.com/telebot/message" \
        "https://api.telegram.org/bot{BOTTOKEN}/setWebhook"
   ```

### 코레일 계정 설정

- **관리자 계정**: `.env` 파일에 `USERID`, `USERPW` 설정
- **사용자 계정**: 봇 사용 시 개별적으로 입력

### 사용자 권한 관리

- **ALLOW_LIST**: 봇 사용을 허용할 전화번호 목록 (콤마로 구분)
- **ADMINPW**: 관리자 기능 접근을 위한 비밀번호

## 배포 가이드

### NGINX 리버스 프록시 설정

텔레그램 웹훅은 특정 포트(80, 88, 443, 8443)와 HTTPS만 지원하므로 NGINX 설정이 필요합니다.

```nginx
# 운영 환경
location /telebot {
  rewrite ^/telebot/(.*) /$1 break;
  proxy_pass http://localhost:8391;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}

# 개발 환경
location /telebot_dev {
  rewrite ^/telebot_dev/(.*) /$1 break;
  proxy_pass http://localhost:8390;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```

### 시스템 서비스 등록

```ini
# /etc/systemd/system/korail-bot.service
[Unit]
Description=Korail Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/korail_telegrambot
ExecStart=/usr/local/bin/pipenv run fastapi run src/app.py --port 8391
Restart=always
RestartSec=3
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

```bash
# 서비스 활성화
sudo systemctl enable korail-bot
sudo systemctl start korail-bot
sudo systemctl status korail-bot
```

## 모니터링 및 로그

### 애플리케이션 로그 확인

```bash
# 개발 모드 로그
tail -f logs/development.log

# 운영 모드 로그
tail -f logs/production.log

# Docker 로그
docker compose logs -f web
```

### Celery 모니터링

```bash
# Flower 웹 인터페이스 (http://localhost:5555)
make celery-flower-start

# Celery 워커 상태 확인
celery -A src.telegramBot.tasks inspect active
```

## 문제 해결

### 일반적인 문제

#### 1. 코레일 로그인 실패
```
코레일 로그인에 실패했습니다.
```
- **해결책**: `.env` 파일의 코레일 계정 정보 확인
- **원인**: 잘못된 계정 정보 또는 코레일 서버 점검

#### 2. 웹훅 설정 실패
```
Failed to set webhook
```
- **해결책**: HTTPS URL 및 SSL 인증서 확인
- **원인**: 잘못된 웹훅 URL 또는 SSL 문제

#### 3. Redis 연결 실패 (MQ 모드)
```
ConnectionError: Error connecting to Redis
```
- **해결책**: Redis 서버 실행 상태 및 연결 설정 확인
- **원인**: Redis 서버 미실행 또는 잘못된 연결 정보

#### 4. 포트 충돌
```
Bind for 0.0.0.0:8390 failed: port is already allocated
```
- **해결책**: `make docker-compose-down` 실행 후 재시작
- **원인**: 이전 컨테이너가 완전히 정리되지 않음

### 성능 최적화

#### Celery 워커 튜닝
```bash
# 동시 처리 작업 수 조정
celery -A src.telegramBot.tasks worker --concurrency=4

# 메모리 제한 설정
celery -A src.telegramBot.tasks worker --max-memory-per-child=200000
```

#### Docker 리소스 제한
```yaml
# docker-compose.yml에서 리소스 제한
services:
  web:
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
```

## 보안 고려사항

### 환경 변수 보안
- `.env` 파일을 절대 git에 커밋하지 마세요
- 운영 환경에서는 환경 변수 또는 비밀 관리 시스템 사용

### 네트워크 보안
- 방화벽 설정으로 필요한 포트만 개방
- HTTPS/TLS 인증서 사용 필수

### 접근 제어
- `ALLOW_LIST`를 통한 사용자 제한
- 관리자 기능은 `ADMINPW`로 보호

## 기여하기

1. 저장소를 포크하세요
2. 기능 브랜치를 생성하세요 (`git checkout -b feature/amazing-feature`)
3. 변경사항을 커밋하세요 (`git commit -m 'Add some amazing feature'`)
4. 브랜치에 푸시하세요 (`git push origin feature/amazing-feature`)
5. Pull Request를 생성하세요

## 라이선스

이 프로젝트는 MIT 라이선스 하에 배포됩니다. 자세한 내용은 LICENSE 파일을 참조하세요.

## 지원

문제 및 질문:
- GitHub에서 이슈 생성
- `logs/` 디렉토리의 로그 확인
- `docker compose logs`로 컨테이너 모니터링

## 면책 조항

1. 이 프로그램은 개인용 목적으로만 사용해야 하며, 상업적 목적으로 사용하는 것을 금합니다.
2. 코레일 서버에 무리가 가지 않도록 기본 설정 값(1초에 1번 조회) 이상으로 빠르게 설정하지 마세요.
3. 과도한 요청으로 인해 계정이 정지될 수 있습니다.
4. 이 프로그램은 현재 시점 기준으로 테스트되었으며, 코레일 서버 업데이트에 따라 작동하지 않을 수 있습니다.