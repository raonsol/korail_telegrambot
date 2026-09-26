# 웹앱(PWA) 지원 설계

> 상태: **구현 완료** · 작성일: 2026-09-26
>
> 구현 과정에서 달라진 점은 [12. 구현 결과](#12-구현-결과-설계-대비-변경점)를 참고하세요.

## 1. 목표와 범위

| 항목 | 내용 |
|------|------|
| 목표 | 텔레그램 봇과 **동일한 예약 기능**을 웹앱에서도 제공 |
| 형태 | PWA: 홈 화면 설치, 오프라인 셸, Web Push 알림 |
| 예약 실행 | **지금처럼 백엔드에서 수행** (subprocess / Celery 두 모드 모두 유지) |
| 인증 | 웹 로그인 화면 필요 (ALLOW_LIST + 코레일 계정 검증, 관리자 로그인) |
| 비목표 | 결제 자동화, 브라우저에서 코레일 API 직접 호출, 텔레그램 봇 제거 |

핵심 원칙은 **"채널(텔레그램/웹)은 얇게, 예약 도메인은 하나로"** 입니다. 지금은 예약 시작·취소·상태 관리가 모두 `TelegramBot` 클래스(`src/telegramBot/bot.py`) 안에 들어 있어서 웹에서 재사용할 수 없습니다. 이 로직을 채널과 무관한 서비스 계층으로 빼내는 것이 이번 작업의 대부분입니다.

---

## 2. 현재 구조와 문제점

```
Telegram ──webhook──▶ FastAPI(/message) ──▶ TelegramBot
                                               ├─ userDict / runningStatus (in-memory)
                                               ├─ subprocess.Popen(worker.py) ──┐
                                               └─ reservation_task.delay() ─────┤
                                                                                ▼
                         FastAPI(/completion/{chat_id}, /reservation_callback) ◀─ HTTP callback
                                               └─ bot.send_message(chat_id)
```

웹앱을 붙이기 전에 알아야 할 제약:

1. **식별자가 `chat_id`에 묶여 있음**: `runningStatus` 값, 콜백 URL(`/completion/{chat_id}`), Celery task 인자(`chat_id`)가 모두 텔레그램 chat_id를 전제로 합니다. 웹 사용자에게는 chat_id가 없습니다.
2. **알림 경로가 텔레그램 전용**: 콜백 핸들러(`app.py`)가 곧바로 `bot.send_message()`를 호출합니다.
3. **대화형 상태 머신**: `userDict[chat_id]["lastAction"]` 1~12단계는 채팅 UX 전용입니다. 웹은 폼 한 번 제출로 끝나므로 재사용 대상이 아닙니다(검증 로직만 재사용).
4. **보안상 약점** (웹 공개 전 반드시 정리):
   - `/completion/*`, `/reservation_callback`에 인증이 없음 → 외부에서 임의 호출로 사용자에게 메시지를 보내거나 상태를 지울 수 있음
   - `CORSMiddleware(allow_origins=["*"])`
   - subprocess 모드에서 코레일 비밀번호가 **argv**로 전달됨(`ps`로 노출), Celery 모드에서는 브로커(Redis) 메시지에 평문으로 저장됨
5. **이미 있는 버그** (리팩터링 때 같이 해결):
   - `/completion/{chat_id}`는 해당 chat_id의 *첫 번째* 예약을 삭제 → 동시 예약 시 엉뚱한 예약이 정리됨
   - `_start_background_process`의 `monitor_process`: `os.kill()`은 `None`을 반환하므로 `if` 분기가 실행되지 않아 **busy loop**가 돌고, `time`이 `datetime.time`이라 `time.sleep`도 동작하지 않음. 또 `runningStatus`를 `chat_id` 키로 지우려 하지만 실제 키는 PID임

---

## 3. 목표 아키텍처

```
              ┌──────────── Telegram ────────────┐         ┌──────── Browser (PWA) ────────┐
              │ webhook                          │         │ HTTPS (same-origin)           │
              ▼                                  │         ▼                               │
   ┌───────────────────────────────────────── FastAPI (app.py) ─────────────────────────────────────┐
   │  /message            → TelegramBot (채널 어댑터: 대화 상태 머신만 담당)                        │
   │  /api/*              → Web API 라우터 (auth, stations, reservations, push, events)            │
   │  /app/*              → PWA 정적 파일 (index.html, manifest, service worker)                   │
   │  /internal/events    → 워커 콜백 (공유 비밀키 인증)                                           │
   │                                                                                                │
   │                  ┌──────────────── core (채널 무관) ────────────────┐                          │
   │                  │ ReservationService  start / cancel / list / on_event                        │
   │                  │ AuthService         login / session / admin                                 │
   │                  │ Stores              InMemory*Store | Redis*Store (모드별)                    │
   │                  │ Notifier            TelegramNotifier, WebPushNotifier, SSEBroker (fan-out)   │
   │                  │ Launcher            SubprocessLauncher | CeleryLauncher                      │
   │                  └──────────────────────────────────────────────────┘                          │
   └────────────────────────────────────────────────────────────────────────────────────────────────┘
                     │ launch                                        ▲ POST /internal/events
                     ▼                                               │ (reservation_id, status, ...)
          worker.py (subprocess)  또는  Celery worker ── korail2 ──▶ Korail
```

### 3.1 디렉터리 구조 (제안)

```
src/
├── app.py                     # 라우터 조립만 (lifespan, include_router, StaticFiles)
├── config.py                  # + 웹앱 설정 추가
├── core/                      # ★ 신규: 채널 무관 도메인
│   ├── models.py              # ReservationRequest, Reservation, ReservationStatus, UserIdentity
│   ├── reservation_service.py # 예약 시작/취소/조회/이벤트 처리
│   ├── auth_service.py        # 로그인, 허용목록 검증, 세션 발급
│   ├── launchers.py           # SubprocessLauncher, CeleryLauncher (bot.py에서 이동)
│   ├── stores.py              # SessionStore / ReservationStore / PushSubscriptionStore (InMemory, Redis)
│   ├── notifier.py            # Notifier 인터페이스 + fan-out
│   └── crypto.py              # 코레일 비밀번호 암·복호화 (Fernet)
├── web/                       # ★ 신규: Web API
│   ├── deps.py                # get_current_user (세션 쿠키), CSRF 검증
│   ├── routes_auth.py
│   ├── routes_reservations.py
│   ├── routes_stations.py
│   ├── routes_push.py
│   ├── routes_events.py       # SSE
│   └── routes_internal.py     # 워커 콜백
└── telegramBot/
    ├── bot.py                 # 대화 흐름만 남기고, 예약 실행은 ReservationService 호출
    ├── tasks.py               # reservation_id 기반으로 변경
    ├── worker.py              # stdin으로 인자 수신, reservation_id 기반 콜백
    └── ...
webapp/                        # ★ 신규: 프론트엔드 (PWA)
├── package.json
├── vite.config.ts             # vite-plugin-pwa
├── public/icons/…
└── src/…
```

---

## 4. 도메인 모델 (core/models.py)

### 4.1 사용자 식별

두 채널 모두 **코레일 로그인 전화번호**로 사용자를 인증하므로, 정규화한 전화번호(`01012345678`)를 공통 사용자 ID로 씁니다.

```python
class UserIdentity(BaseModel):
    user_id: str                 # 정규화된 전화번호, 관리자는 "admin"
    is_admin: bool = False
    telegram_chat_id: int | None = None   # 텔레그램에서 로그인한 적이 있으면 연결
```

이렇게 하면 텔레그램으로 시작한 예약을 웹에서 조회·취소하거나, 웹에서 시작한 예약 결과를 텔레그램으로도 받을 수 있습니다(연결된 경우).

### 4.2 예약

```python
class ReservationStatus(str, Enum):
    QUEUED = "queued"; RUNNING = "running"; SUCCESS = "success"
    FAILED = "failed"; CANCELLED = "cancelled"; ERROR = "error"

class ReservationRequest(BaseModel):     # 웹 폼 / 봇 대화 결과를 모두 이 형태로 변환
    dep_date: date
    src_station: str
    dst_station: str
    dep_time: str      # "HHMM"
    max_dep_time: str  # "HHMM"
    train_type: Literal["KTX", "ALL"]
    seat_type: Literal["general", "general_only", "special", "special_only"]

class Reservation(BaseModel):
    id: str                       # uuid4, 서비스가 발급 (task_id/pid와 분리)
    owner_id: str                 # UserIdentity.user_id
    origin: Literal["telegram", "web"]
    request: ReservationRequest
    status: ReservationStatus
    runner: Literal["subprocess", "celery"]
    runner_ref: str | None        # PID 또는 Celery task_id (취소용)
    attempts: int = 0
    result_text: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
```

**변경 포인트**: 지금은 `runningStatus`의 키가 PID 또는 Celery task_id입니다. 이를 **서비스가 발급한 `reservation_id`** 로 바꾸고, PID/task_id는 `runner_ref`로 내립니다. 워커·콜백·취소가 모두 `reservation_id` 하나로 동작하므로 두 모드의 분기가 `Launcher` 안으로 들어갑니다. CLAUDE.md의 "task_id 단위로 독립" 원칙은 그대로 유지됩니다.

검증 규칙(오늘 날짜면 과거 시각 불가, `HHMM` 형식, `max_dep_time > dep_time` 등)은 현재 `bot.py`의 `is_valid_time`, `is_past_time`에 흩어져 있는데, `ReservationRequest`의 pydantic validator로 옮겨 봇과 웹이 함께 씁니다.

---

## 5. 백엔드 설계

### 5.1 ReservationService

```python
class ReservationService:
    def __init__(self, store: ReservationStore, launcher: Launcher,
                 notifier: Notifier, creds: CredentialVault): ...

    async def start(self, user: UserIdentity, req: ReservationRequest,
                    origin: str) -> Reservation
    async def cancel(self, user: UserIdentity, reservation_id: str) -> bool
    async def cancel_all(self, user: UserIdentity) -> int
    async def list(self, user: UserIdentity, active_only=True) -> list[Reservation]
    async def get(self, user: UserIdentity, reservation_id: str) -> Reservation
    async def on_worker_event(self, event: WorkerEvent) -> None   # 콜백 진입점
```

- `start`: 요청 검증 → 동시 예약 수 제한(`max_concurrent_reservations`, 사용자별 한도도 추가 권장) → `Reservation` 저장(QUEUED) → `launcher.launch()` → `runner_ref` 저장.
- `cancel`: 소유자 확인(관리자는 전체 가능) → `launcher.cancel(runner_ref)` → CANCELLED 저장 → 알림.
- `on_worker_event`: 상태 갱신 후 `notifier.notify(owner_id, event)`로 fan-out.
- 봇의 `_start_reserve`, `_cancel_reservation`, `_show_cancel_menu`, `cancel_all`은 이 서비스를 호출하도록 얇아집니다.

### 5.2 Launcher (모드별 실행기)

| | SubprocessLauncher | CeleryLauncher |
|---|---|---|
| 실행 | `Popen([python, -m, telegramBot.worker])` | `reservation_task.apply_async(task_id=reservation_id)` |
| 인자 전달 | **stdin으로 JSON** 전달 (argv 금지) | 비밀번호는 **암호화된 값**으로 전달, 워커가 복호화 |
| 취소 | `os.killpg(SIGTERM)` | `control.revoke(terminate=True)` |
| 종료 감지 | `asyncio` 태스크로 `proc.wait()` 감시 (기존 스레드 busy loop 대체) | 워커 콜백 |

Celery에서 `task_id=reservation_id`로 지정하면 `reservation_task:{task_id}` Redis 키 구조와 멱등성 체크를 그대로 쓸 수 있습니다.

### 5.3 워커 콜백 통일

기존 `/completion/{chat_id}`(query string)와 `/reservation_callback`(JSON)을 하나로 합칩니다.

```
POST /internal/events
X-Internal-Token: <INTERNAL_CALLBACK_SECRET>
{
  "reservation_id": "…",
  "status": "running" | "progress" | "success" | "failed" | "error",
  "attempts": 150,
  "message": "…",
  "train_info": "…"          # success일 때
}
```

- `hmac.compare_digest`로 토큰 검증, 실패 시 403.
- 콜백 URL은 설정값(`INTERNAL_CALLBACK_URL`)으로 받아서 `korail_client.py`의 하드코딩된 `127.0.0.1:{port}`와 `bot.py`의 `web_celery:8391` 분기를 없앱니다.
- 가능하면 리버스 프록시에서 `/internal/*`를 외부로 노출하지 않습니다.
- 전환 기간에는 기존 두 엔드포인트를 새 핸들러로 위임하는 얇은 호환 레이어를 둡니다.

### 5.4 Notifier (알림 fan-out)

```python
class Notifier(Protocol):
    async def notify(self, owner_id: str, event: ReservationEvent) -> None

class CompositeNotifier:           # 등록된 모든 채널로 전송, 한 채널 실패가 다른 채널을 막지 않음
    channels = [TelegramNotifier, SSEBroker, WebPushNotifier]
```

- **TelegramNotifier**: `owner_id → telegram_chat_id` 매핑이 있으면 기존 `Messages` 템플릿으로 전송.
- **SSEBroker**: 앱이 열려 있을 때 실시간 갱신. 사용자별 `asyncio.Queue` 목록. Celery 모드에서 웹 인스턴스를 여러 대 띄울 경우 Redis Pub/Sub(`events:{owner_id}`)로 브로드캐스트.
- **WebPushNotifier**: 앱이 닫혀 있어도 결과 알림. `pywebpush` + VAPID 키. 예약 성공은 "20분 내 결제 필요"이므로 **푸시가 PWA의 핵심 기능**입니다.
  - 알림 대상 이벤트: `success`, `failed`, `error`, `cancelled`(다른 기기에서 취소 시). `progress`는 SSE로만 보냄.
  - 410/404 응답을 받은 구독은 삭제.

### 5.5 인증·세션 (AuthService)

#### 로그인 흐름

```
[로그인 화면] 전화번호 + 코레일 비밀번호
      │ POST /api/auth/login
      ▼
1) 전화번호 형식 검증 (010, 11자리)
2) ALLOW_LIST 검증 ─ 미등록 → 401 (+ 기존처럼 구독자 브로드캐스트)
3) 로그인 실패 횟수 확인 ─ 한도 초과 → 429   ※ 코레일은 5회 실패 시 계정 잠금
4) korail2 로그인 (run_in_threadpool, 블로킹 호출)
5) 성공 → 세션 생성, 비밀번호는 암호화하여 세션에 보관
6) Set-Cookie: session=<opaque id>; HttpOnly; Secure; SameSite=Lax; Path=/
```

관리자 로그인은 로그인 화면의 "관리자" 탭에서 `ADMINPW`만 입력 → `ADMIN_KORAIL_ID/PW`로 코레일 로그인 (현재 봇의 `_start_accept` 동작과 같음). 관리자는 `is_admin=True`로 전체 예약 조회·취소가 가능합니다.

#### 설계 결정

| 결정 | 선택 | 이유 |
|------|------|------|
| 세션 방식 | **서버 세션 + 불투명 쿠키** (JWT 아님) | 코레일 비밀번호를 서버에 보관해야 하고, 즉시 로그아웃·강제 만료가 필요 |
| 저장소 | subprocess 모드: InMemory / Celery 모드: Redis (`session:{sid}`, TTL) | 기존 모드별 저장 전략과 동일 |
| 비밀번호 보관 | Fernet(`WEBAPP_ENC_KEY`) 암호화, 세션 TTL과 함께 만료 | 예약 시작 시 워커에 넘겨야 함. 클라이언트에는 절대 반환하지 않음 |
| 세션 수명 | 기본 7일 슬라이딩, "로그인 유지" 해제 시 브라우저 세션 | 설치형 PWA에서 매번 로그인하는 불편 방지 |
| CSRF | `SameSite=Lax` + 변경 요청에 `X-CSRF-Token` (double-submit) | 쿠키 인증이므로 필요 |
| 로그인 실패 제한 | 전화번호별 3회/10분, IP별 20회/10분 | 코레일 계정 잠금(5회) 전에 차단 |

> **참고:** 비밀번호를 저장하지 않는 대안(로그인할 때마다 입력)도 가능하지만, 예약이 몇 시간 도는 동안 워커가 재로그인해야 하므로(`tasks.py`의 re-login 로직) 결국 워커 쪽에는 비밀번호가 필요합니다. 따라서 "암호화해서 짧게 보관하고, 로그아웃하면 삭제"를 기본값으로 합니다.

#### 텔레그램 계정 연결 (선택, 2단계)

- 봇에서 코레일 로그인에 성공하면 `user_id ↔ chat_id` 매핑을 저장 → 웹 예약 결과도 텔레그램으로 받을 수 있음.
- 웹 설정 화면에서 "텔레그램 알림 받기" 토글로 on/off.
- (추후) Telegram Login Widget으로 웹 로그인하는 방식도 추가 가능.

### 5.6 Web API 명세

모든 경로 prefix `/api`, JSON, 세션 쿠키 인증(로그인 제외).

| Method | Path | 설명 |
|--------|------|------|
| POST | `/auth/login` | `{phone, password, remember}` → 세션 쿠키, `{user, csrf_token}` |
| POST | `/auth/admin-login` | `{admin_password}` |
| POST | `/auth/logout` | 세션·암호화 비밀번호 삭제 |
| GET | `/auth/me` | 현재 사용자 (앱 시작 시 로그인 여부 확인) |
| GET | `/stations?q=광&page=1` | 기존 `search_stations()` 재사용, 서버 측 캐시(TTL 1일) 권장 |
| GET | `/reservations?active=true` | 내 예약 목록 (관리자: `?all=true`) |
| POST | `/reservations` | `ReservationRequest` → `201 Reservation` |
| GET | `/reservations/{id}` | 상세 |
| DELETE | `/reservations/{id}` | 취소 |
| DELETE | `/reservations` | 모두 취소 |
| GET | `/events` | SSE 스트림 (`event: reservation`, `data: Reservation`) |
| GET | `/push/vapid-public-key` | Web Push 공개키 |
| POST | `/push/subscriptions` | 브라우저 PushSubscription 등록 |
| DELETE | `/push/subscriptions` | 해제 |

에러는 `{"code": "LOGIN_FAILED" | "NOT_ALLOWED" | "RATE_LIMITED" | "VALIDATION" | ..., "message": "…"}` 형식으로 통일하고, 사용자에게 보이는 문구는 `Messages`와 공유합니다.

### 5.7 app.py 변경

```python
app = FastAPI(lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
...
app.include_router(internal_router)            # /internal/events
app.mount("/app", StaticFiles(directory="webapp_dist", html=True), name="webapp")
```

- `ENABLE_TELEGRAM`(기본 true) / `ENABLE_WEBAPP`(기본 false) 플래그 추가. 텔레그램을 끄면 lifespan에서 webhook 등록을 건너뜀 (지금은 webhook URL이 없으면 서버가 시작되지 않음).
- CORS: 프론트를 같은 오리진에서 서빙하므로 `/api`에는 CORS를 두지 않거나 `WEBAPP_ORIGIN`만 허용. `allow_origins=["*"]` 제거.
- SPA fallback: `/app/*`의 알 수 없는 경로는 `index.html` 반환.

---

## 6. 프론트엔드 (PWA)

### 6.1 기술 선택 (권장)

| 항목 | 선택 | 비고 |
|------|------|------|
| 빌드 | **Vite + TypeScript** | 빌드 결과를 FastAPI가 정적 서빙 → 별도 서버 불필요 |
| UI | **React** (또는 경량을 원하면 Preact, 코드 호환) | 폼 + 목록 위주라 규모가 작음 |
| PWA | `vite-plugin-pwa` (Workbox) | manifest, service worker 자동 생성, `injectManifest`로 push 핸들러 추가 |
| 서버 상태 | TanStack Query | 목록 캐시, SSE 이벤트로 캐시 갱신 |
| 스타일 | CSS Modules 또는 Tailwind | 모바일 우선 |

> 빌드 단계 없이 가자는 대안(FastAPI + Jinja + htmx)도 가능하지만, 오프라인 셸·푸시 구독·설치 흐름을 다루려면 결국 JS가 꽤 필요하므로 SPA를 권장합니다.

### 6.2 화면 구성

```
/app/login            로그인 (일반 | 관리자 탭)
/app/                 홈: 진행 중 예약 카드 목록 + [새 예약] 버튼 + 최근 결과
/app/new              새 예약 (한 화면 폼)
/app/r/:id            예약 상세: 상태, 시도 횟수, 결과, [취소]
/app/settings         알림(푸시/텔레그램) 설정, 설치 안내, 로그아웃
```

**새 예약 폼**: 봇의 4~10단계를 한 화면으로 합칩니다.

| 봇 단계 | 웹 컴포넌트 |
|---------|-------------|
| 4 날짜 (`calendar_keyboard`) | 날짜 선택기 (오늘 ~ 예매 가능일) |
| 5·6 출발/도착역 (`station_keyboard`) | 자동완성 콤보박스 (`/api/stations`, 300ms 디바운스), ⇄ 교체 버튼, 최근 사용 역 |
| 7·8 출발 시각/최대 출발 시각 (`time_keyboard`) | 시간 범위 선택 (오늘이면 과거 시각 비활성) |
| 9 열차 종류 | 세그먼트 버튼 (KTX / 전체) |
| 10 좌석 | 세그먼트 버튼 (일반 우선 / 일반만 / 특실 우선 / 특실만) |
| 11 확인 | 제출 전 요약 시트 |

**로그인 화면**:
- 전화번호 입력(자동 하이픈), 비밀번호, "로그인 유지" 체크
- 오류 메시지: 미등록 사용자 / 로그인 실패(남은 시도 횟수 안내, 코레일 5회 잠금 경고) / 잠시 후 재시도
- 개인정보 안내: "비밀번호는 예약 수행을 위해 서버에 암호화되어 임시 저장되며 로그아웃 시 삭제됩니다"

### 6.3 PWA 요구사항

- **manifest.webmanifest**: `name`, `short_name`, `start_url: "/app/"`, `scope: "/app/"`, `display: "standalone"`, `theme_color`, 192/512 아이콘 + maskable 아이콘.
- **Service Worker**:
  - 앱 셸(HTML/JS/CSS/아이콘) precache → 오프라인에서도 앱이 열림
  - `/api/*`는 **캐시하지 않음**(network-only). 오프라인이면 "오프라인입니다" 배너
  - `push` 이벤트 → `showNotification`, `notificationclick` → `/app/r/:id` 열기
  - 새 버전 배포 시 "업데이트 있음 → 새로고침" 토스트 (`registerType: "prompt"`)
- **HTTPS 필수** (service worker, push). 운영 도메인은 기존 webhook 도메인을 재사용하면 됨.
- **iOS 주의**: Web Push는 iOS 16.4+에서 **홈 화면에 설치한 경우에만** 동작. 설정 화면에 설치 안내를 넣고, 푸시가 불가능한 환경에서는 텔레그램 연결을 권장.
- 푸시 권한 요청은 첫 예약을 시작하는 시점에 맥락과 함께 요청 (앱 진입 즉시 요청하지 않음).

### 6.4 실시간 상태 반영

1. 앱 포그라운드: `EventSource('/api/events')` → 수신 시 TanStack Query 캐시 갱신. 연결이 끊기면 자동 재연결 + 재연결 시 목록 재조회.
2. 앱 백그라운드/종료: Web Push.
3. SSE를 쓸 수 없는 환경: 홈 화면에서 15초 폴링으로 대체.

---

## 7. 모드별 동작 정리

| | Subprocess 모드 | Celery(MQ) 모드 |
|---|---|---|
| 세션/예약/푸시 구독 저장 | InMemory (재시작 시 소실 → 재로그인 필요) | Redis (`session:*`, `reservation:*`, `push:*`, `user:*`) |
| SSE 브로드캐스트 | 프로세스 내 큐 | Redis Pub/Sub (웹 인스턴스 여러 대 가능) |
| 예약 실행 | `worker.py` (stdin 인자) | `reservation_task` (`task_id=reservation_id`) |
| 추가 의존성 | `cryptography`, `pywebpush` | 동일 |

Subprocess 모드는 지금처럼 가볍게 유지합니다. 웹앱은 옵션(`ENABLE_WEBAPP`)이고, 켜더라도 추가 프로세스는 없습니다.

---

## 8. 설정 추가 (config.py / .env)

```bash
ENABLE_TELEGRAM=true
ENABLE_WEBAPP=true
WEBAPP_ORIGIN=https://example.com          # CORS/CSRF 검증용
WEBAPP_ENC_KEY=                             # Fernet 키 (코레일 비밀번호 암호화)
SESSION_TTL_HOURS=168
INTERNAL_CALLBACK_URL=http://127.0.0.1:8391/internal/events   # Docker: http://web_celery:8391/internal/events
INTERNAL_CALLBACK_SECRET=
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_SUBJECT=mailto:admin@example.com
LOGIN_MAX_FAILURES=3
```

기존 `secret_key: str = "your-secret-key-here"` 기본값은 제거하고, 웹앱을 켰는데 필수 비밀값이 비어 있으면 시작 시 실패하도록 합니다.

---

## 9. 배포

- **Dockerfile 멀티 스테이지**: `node:22-alpine`에서 `webapp` 빌드 → `python` 스테이지에 `webapp_dist/`만 복사. Node 런타임은 최종 이미지에 포함되지 않음.
- **docker-compose**: 서비스 추가 없음. `web` / `web_celery`에 환경변수만 추가.
- **리버스 프록시**: `/message`(텔레그램), `/api`, `/app` 공개. `/internal`은 차단. SSE 경로는 버퍼링 끄기(`proxy_buffering off`), 긴 read timeout.
- **Makefile**: `make webapp-dev`(Vite dev server, `/api`를 8390으로 프록시), `make webapp-build`.
- **CI**: `test.yml`에 `webapp` lint/typecheck/build 잡 추가, 기존 Docker 빌드 테스트가 멀티 스테이지를 검증.

---

## 10. 단계별 구현 계획

각 단계는 독립적으로 머지 가능하고, 텔레그램 봇 동작은 단계마다 그대로 유지됩니다.

| 단계 | 내용 | 산출물 |
|------|------|--------|
| **0. 정리** | 콜백 인증(공유 비밀키), CORS 축소, subprocess 인자를 stdin으로, `monitor_process` 버그 수정, `/completion`의 chat_id 매칭 버그 수정 | 보안·버그 수정 PR |
| **1. core 추출** | `core/models.py`, `ReservationService`, `Launcher`, `Store`(InMemory/Redis), `reservation_id` 도입, `/internal/events`로 콜백 통일(구 엔드포인트는 위임), 봇이 서비스를 호출하도록 리팩터링 | 봇 동작 동일, 기존 테스트 통과 + 서비스 단위 테스트 |
| **2. Web API** | `AuthService`(세션·암호화·실패 제한), `/api/*` 라우터, SSE, `ENABLE_*` 플래그 | OpenAPI 문서, API 테스트 |
| **3. 프론트엔드** | Vite+React 앱: 로그인, 홈, 새 예약, 상세, 설정. PWA manifest·service worker(오프라인 셸) | `/app`에서 설치 가능한 PWA |
| **4. 푸시·연동** | VAPID, `WebPushNotifier`, 구독 API, 텔레그램 계정 연결 | 앱을 닫아도 결과 알림 |
| **5. 배포** | 멀티 스테이지 Dockerfile, Makefile, CI 잡, 문서(CLAUDE.md, README) 갱신 | 운영 배포 |

### 테스트 전략

- **unit**: `ReservationRequest` 검증, `ReservationService`(가짜 Launcher/Store/Notifier), `AuthService`(korail2 로그인 mock, 실패 제한, 세션 만료), 암호화 왕복.
- **integration**: `httpx.AsyncClient`로 `/api/*` 흐름(로그인 → 예약 생성 → `/internal/events` 주입 → SSE 수신 → 취소), subprocess/celery 마커별로 실행 (`fakeredis` 활용).
- **e2e (선택)**: Playwright로 로그인 → 예약 폼 제출 → 상태 반영, Lighthouse PWA 설치 가능 여부 점검.

---

## 11. 확정된 결정사항

| 항목 | 결정 |
|------|------|
| 비밀번호 보관 | 세션 동안 Fernet 암호화 보관, 로그아웃/만료 시 삭제 |
| 프론트엔드 스택 | Vite + React + TypeScript + vite-plugin-pwa + TanStack Query |
| 사용자 관리 | **DB** (`users` 테이블). `ALLOW_LIST`는 최초 시드 용도, 관리자 화면/봇 명령으로 관리 |
| 예약 이력 보존 | **30일** (`RESERVATION_RETENTION_DAYS`) |

## 12. 구현 결과 (설계 대비 변경점)

| 설계 | 구현 | 이유 |
|------|------|------|
| 세션/예약 저장소를 모드별로 InMemory/Redis | 두 모드 모두 **SQLAlchemy DB** (subprocess: SQLite, Celery: PostgreSQL) | 사용자 DB 관리와 30일 이력 요구로 DB가 필요해졌고, 저장소를 하나로 통일하면 재시작에도 세션·이력이 유지됨 |
| 워커 콜백을 공유 비밀키(`INTERNAL_CALLBACK_SECRET`)로 인증 | **예약별 1회용 토큰** (DB에는 SHA-256만 저장) | 별도 비밀값 설정이 필요 없고, 한 예약의 토큰으로 다른 예약을 조작할 수 없음 |
| SSE 멀티 인스턴스용 Redis Pub/Sub | 프로세스 내 브로커 | 현재 compose 구성은 웹 인스턴스 1개. 여러 대로 늘릴 때 추가 |
| `DELETE /api/push/subscriptions` | `POST /api/push/unsubscribe` | 본문(endpoint)이 있는 DELETE를 피함 |
| 공통 예약 루프 없음 | `core/runner.py`로 subprocess/Celery 루프 통합 | 두 모드의 재시도·재로그인·진행 보고 로직 중복 제거 |
| 사용자별 동시 예약 한도 권장 | `MAX_RESERVATIONS_PER_USER=3` (관리자 제외) + 전체 한도 | 기존 봇의 "다른 사용자 이용 중" 전역 차단을 대체 |

추가로 구현한 것:
- 응답이 끊긴 예약 자동 정리 (RUNNING 30분 무응답, QUEUED 24시간)
- 봇 관리자 명령 `/users`, `/adduser`, `/deluser`
- 텔레그램 봇의 좌석 옵션이 Celery 모드에서 항상 "일반실 우선"으로 전달되던 문제 수정
- 로컬 `make dev-mq`에서 콜백 주소가 Docker 호스트명(`web_celery`)으로 고정되던 문제 수정 (`INTERNAL_CALLBACK_URL`, 기본값 로컬 포트)
- 오프라인에서도 마지막 사용자 정보로 앱 셸 표시, 날짜/시각은 클라이언트·서버 모두 KST 기준으로 검증

남은 과제:
- DB 마이그레이션 도구(Alembic) 도입 — 현재는 `create_all`로 테이블 생성만 수행
- 웹 인스턴스를 여러 대로 늘릴 경우 SSE용 Redis Pub/Sub, 로그인 실패 카운터의 공유 저장소
