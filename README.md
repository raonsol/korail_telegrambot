# KTX 예약 텔레그램 봇 & 웹앱

이중 실행 모드와 포괄적인 Docker 지원을 갖춘 한국철도(KTX) 자동 예약 서비스입니다.
텔레그램 봇과 설치형 웹앱(PWA) 두 가지 방법으로 이용할 수 있으며, 예약은 모두 백엔드에서 수행됩니다.

## 특징

- 🚄 **자동 KTX 예약**: 재시도 로직을 통한 자동 기차표 예약
- 🔄 **이중 실행 모드**: 경량 subprocess 모드 또는 확장 가능한 MQ 모드 선택
- 📱 **대화형 인터페이스**: 캘린더 기반 날짜 선택 및 시간 선호도 설정
- 🐳 **Docker 지원**: 개발 및 운영 환경을 위한 완전한 컨테이너화
- 🔐 **인증 시스템**: 전화번호 인증 및 안전한 사용자 관리
- 📊 **실시간 업데이트**: 웹훅 기반 상태 알림
- 🌐 **웹앱(PWA)**: 홈 화면 설치, 오프라인 셸, 푸시 알림, 로그인 화면, 관리자 화면
- 🗂️ **사용자 DB 관리 / 30일 예약 이력**: SQLite(기본) 또는 PostgreSQL

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
ADMIN_KORAIL_ID=코레일_사용자명
ADMIN_KORAIL_PW=코레일_비밀번호

# 사용자 (최초 실행 시 DB에 등록, 이후에는 관리자 화면/봇 명령으로 관리)
ALLOW_LIST=전화번호1,전화번호2,전화번호3
ADMINPW=관리자_비밀번호

# 웹앱 (선택사항)
ENABLE_WEBAPP=true
WEBAPP_ENC_KEY=임의의_긴_문자열     # 코레일 비밀번호 암호화 키 (openssl rand -base64 32)
VAPID_PUBLIC_KEY=                  # make vapid-keys 로 생성 (푸시 알림)
VAPID_PRIVATE_KEY=

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
| `make korail-login-check` | Docker 안에서 관리자 계정 로그인 진단 (받은 ID/PW 상태, 코레일 원본 응답 표시. 비밀번호는 출력하지 않으며 로그인 1회로 집계) |

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
| `make webapp-build` | 웹앱(PWA) 빌드 |
| `make webapp-dev` | 웹앱 개발 서버 (5173 포트) |
| `make vapid-keys` | 푸시 알림용 VAPID 키 생성 |

## 웹앱 (PWA)

텔레그램 없이 브라우저/홈 화면 앱에서 예약할 수 있습니다. 예약 실행은 텔레그램과 같은 백엔드 워커가 담당합니다.

### 화면
- **로그인**: 코레일 계정(전화번호 + 비밀번호) 또는 관리자 비밀번호
- **홈**: 진행 중인 예약(실시간 갱신) + 최근 30일 이력
- **새 예약**: 날짜, 역 검색(자동완성), 출발 시각 범위, 열차/좌석 종류
- **예약 상세**: 진행 상태·시도 횟수, 성공 시 결제 링크, 취소, 같은 조건으로 다시 예약
- **설정**: 푸시 알림, 텔레그램 알림, 앱 설치 안내, 로그아웃
- **관리**(관리자): 사용자 추가/비활성화/삭제, 전체 예약 조회·취소

### 실행

```bash
# 1. 웹앱 빌드 (webapp/dist → FastAPI가 /app 에서 서빙)
make webapp-build

# 2. .env에 ENABLE_WEBAPP=true, WEBAPP_ENC_KEY 설정 후 서버 실행
make dev          # http://localhost:8390/app/

# 프론트엔드 개발 시 (핫 리로드, /api는 8390으로 프록시)
make webapp-dev   # http://localhost:5173/app/

# 푸시 알림용 VAPID 키 생성
make vapid-keys
```

Docker 이미지는 멀티 스테이지 빌드로 웹앱을 함께 빌드하므로 별도 작업이 필요 없습니다.

### 배포 시 주의사항
- **HTTPS 필수**: 서비스 워커·푸시 알림·보안 쿠키가 HTTPS에서만 동작합니다. 기존 webhook 도메인을 그대로 사용하면 됩니다.
- **`/internal` 경로 차단**: 워커 콜백 전용입니다(예약별 토큰으로 인증). 리버스 프록시에서 외부 노출을 막는 것을 권장합니다.
- **SSE**: `/api/events`는 프록시 버퍼링을 끄고(`proxy_buffering off`) 읽기 타임아웃을 길게 설정하세요.
- **iPhone**: 푸시 알림은 iOS 16.4 이상에서 **홈 화면에 추가한 경우에만** 동작합니다.
- **WEBAPP_ENC_KEY**: 설정하지 않으면 재시작 시 웹 세션이 만료되고, Celery 브로커에 비밀번호가 평문으로 전달됩니다. 웹 서버와 Celery 워커에 같은 값을 설정하세요.

### 보안 설계
- 세션: 서버 저장 세션 + HttpOnly/SameSite 쿠키, 변경 요청에는 CSRF 토큰 필요
- 코레일 비밀번호: 예약 실행을 위해 암호화(Fernet)하여 세션에만 보관, 로그아웃/만료 시 삭제
- 로그인 제한: 전화번호당 10분에 3회 실패 시 잠금 (코레일은 5회 실패 시 계정 잠금)
- subprocess 워커에는 stdin으로 전달하여 프로세스 목록(`ps`)에 비밀번호가 노출되지 않음

설계 배경은 [docs/webapp-design.md](docs/webapp-design.md)를 참고하세요.

## 사용자 관리

사용자는 DB에서 관리합니다. `ALLOW_LIST`는 최초 실행 시 사용자 DB를 채우는 용도로만 사용되며, 이후 목록에서 번호를 빼도 DB의 사용자는 그대로 유지됩니다.

- **웹 관리 화면**: 관리자 로그인 → 관리 → 사용자 (추가, 비활성화, 삭제)
- **텔레그램 봇**: `/start` 후 관리자 비밀번호로 로그인한 채팅에서
  - `/users` - 등록된 사용자 목록
  - `/adduser 010-1234-5678 [이름]` - 사용자 등록
  - `/deluser 010-1234-5678` - 사용자 삭제

사용자를 비활성화/삭제하면 해당 사용자의 웹 세션도 즉시 만료됩니다.

## 모드 선택 가이드

모드별 처리량 측정 결과(2026-09-26 측정, 서버 크기별 동시 예약 수 등)는 [docs/capacity-review.md](docs/capacity-review.md)를 참고하세요.

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

### 워커 상태 보고 (subprocess / MQ 공통)
```
POST /internal/events   {"reservation_id", "token", "status", "attempts", "message", "train_info"}
```

### 웹앱 API (ENABLE_WEBAPP=true)
전체 명세는 `/api/docs`에서 확인할 수 있습니다.
```
POST   /api/auth/login | /api/auth/admin-login | /api/auth/logout
GET    /api/auth/me
GET    /api/reservations?status=active|history|all&scope=mine|all
POST   /api/reservations
GET    /api/reservations/{id}
DELETE /api/reservations/{id}
GET    /api/stations?q=
GET    /api/events                (SSE)
POST   /api/push/subscriptions | /api/push/unsubscribe
GET    /api/admin/users           (관리자)
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
│   ├── app.py                # FastAPI 애플리케이션 조립 (서비스, 봇, 웹 API)
│   ├── config.py             # 설정 관리
│   ├── core/                 # 채널 무관 예약 도메인
│   │   ├── reservations.py   # 예약 시작/취소/조회, 워커 보고, 30일 이력 정리
│   │   ├── auth.py           # 웹 로그인/세션
│   │   ├── users.py          # 사용자 DB 관리
│   │   ├── launchers.py      # subprocess / Celery 실행기
│   │   ├── runner.py         # 워커 공통 예약 루프
│   │   ├── notifier.py       # 텔레그램/SSE/Web Push 알림
│   │   └── models.py         # DB 모델
│   ├── web/                  # 웹앱 REST API, PWA 정적 파일 서빙
│   └── telegramBot/
│       ├── bot.py            # 텔레그램 대화 흐름
│       ├── tasks.py          # Celery 작업
│       ├── worker.py         # Subprocess 워커
│       ├── korail_client.py  # 코레일 API 클라이언트
│       └── messages.py       # 메시지 템플릿
├── webapp/                   # PWA (Vite + React + TypeScript)
├── docs/webapp-design.md     # 웹앱 설계 문서
├── docker-compose.yml        # 운영 Docker 설정
├── Dockerfile                # 멀티 스테이지 빌드 (웹앱 + 서버)
├── Makefile                  # 빌드 및 실행 명령어
├── Pipfile                   # Python 의존성
├── CLAUDE.md                 # AI 어시스턴트 지시사항
└── README.md                 # 이 파일
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

- **관리자 계정**: `.env` 파일에 `ADMIN_KORAIL_ID`, `ADMIN_KORAIL_PW` 설정
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
- 사용자 DB(최초 `ALLOW_LIST`로 시드)를 통한 사용자 제한
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