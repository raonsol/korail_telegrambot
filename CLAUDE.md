# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

KTX (Korean train) reservation automation with **two channels** — a Telegram bot and a web app (PWA) — sharing one channel-agnostic reservation domain. Reservations always run on the backend, with a dual-mode architecture supporting both lightweight subprocess execution and scalable distributed processing.

### Technology Stack

- **FastAPI**: Telegram webhook, web app REST API, worker callbacks, PWA static files
- **python-telegram-bot**: Telegram Bot API interactions
- **pykorail** (`==0.2.1`): KTX reservation API client library (코레일톡 앱 API, curl_cffi 기반)
- **SQLAlchemy + Alembic**: Users, web sessions, reservations (+30-day history), push subscriptions; schema migrations run on web startup
  - SQLite (local / subprocess mode), PostgreSQL (Docker Celery mode)
- **Redis + Celery**: Optional distributed task processing system (MQ pattern)
- **React + Vite + vite-plugin-pwa** (`webapp/`): Installable PWA with offline shell and Web Push
- **Docker**: Multi-stage build (Node builds the PWA, Python image serves it)

### Layers

```
Telegram ─webhook─▶ /message ─▶ telegramBot/bot.py (conversation state only) ─┐
Browser (PWA) ────▶ /api/*   ─▶ web/routes_*.py (session cookie + CSRF) ──────┤
                                                                              ▼
                     core/ (channel-agnostic)
                       ReservationService ─ Launcher ─▶ worker (subprocess | Celery)
                       AuthService / UserService            │  pykorail loop (core/runner.py)
                       Notifier ─▶ Telegram, SSE, Web Push  │
                          ▲                                 │
                          └── /internal/events ◀────────────┘ (per-reservation token)
```

### Execution Modes

#### Subprocess Mode (Default/Lightweight)
- **Purpose**: Single-user deployments, development, testing
- **Storage**: SQLite (`DATABASE_URL`, default `sqlite:///./korail_bot.db`) + in-memory conversation state (`userDict`)
- **Background Tasks**: one process per reservation, forked from a `multiprocessing` forkserver that preloads `telegramBot.worker` (~8MB PSS each instead of ~22MB for a fresh interpreter); entry `telegramBot.worker.run_process(spec, log_path)`; the spec travels over the forkserver socket/pipe (never argv)
- **Restart**: workers are `daemon=True`, so they stop with the web server (a non-daemon worker would make every shutdown/reload wait for all reservations). On startup `ReservationService.abort_interrupted()` marks leftover subprocess reservations `error` and notifies users. Do not patch `multiprocessing` internals to keep workers alive
- **Dependencies**: Minimal - only FastAPI web server
- **Scaling**: Vertical scaling only (single server)

#### Celery Mode (MQ Pattern, Distributed/Scalable)
- **Purpose**: Multi-user deployments, production environments
- **Storage**: PostgreSQL (Docker) for users/sessions/reservations; Redis as broker/result backend
- **Background Tasks**: `reservation_task(spec)` published with `task_id=reservation_id`
- **Worker pool**: `CELERY_POOL` (default `threads`; `prefork` optional). The CLI `--pool` default (prefork) overrides `app.conf.worker_pool`, so compose / Makefile pass `--pool=${CELERY_POOL:-threads}`. Concurrency = `CELERY_CONCURRENCY` or `MAX_CONCURRENT_RESERVATIONS`
- **Cancel**: `CeleryLauncher.cancel` writes `status=cancelled` to `reservation_task:{id}` (checked by `should_stop` before every attempt) and revokes; `terminate=True` only with prefork (threads cannot kill)
- **Worker restart detection** (keys in `core/task_state.py`):
  - graceful stop (deploy, `docker stop`): `worker_shutting_down` sets a flag → running tasks stop before their next attempt and report `error` ("예약 워커가 다시 시작되어...") within seconds (threads pool only; compose `stop_grace_period: 40s`)
  - crash / OOM / SIGKILL, or the stop report failed: each worker process refreshes `reservation_hb:{id}` (TTL 60s, every 15s) for its running tasks and deletes it when a task ends; the web server checks every 60s (`ReservationService.detect_lost_workers`) and marks reservations whose state is `running` but have no heartbeat as `error` + sets `cancelled` so a redelivered task does not start. Tasks that never started (no state key) or ran without Redis are left to the 30-min rule
- **Time limit**: the shared loop enforces `spec["max_duration"]` (`RESERVATION_TIMEOUT`, default 3600s) because the threads pool ignores Celery time limits; `visibility_timeout` = 2 × timeout so running tasks are not redelivered
- **Dependencies**: Redis, PostgreSQL, Celery workers
- **Scaling**: Horizontal scaling of workers

Both modes share the same retry loop (`core/runner.py::run_reservation`) and report to `/internal/events`.

**Unreported success** (the ticket is reserved even if the report is lost): `run_reservation` calls `on_success(train_info=, attempts=)` *before* reporting (subprocess: `logs/result_{id}.json`, Celery: `train_info`/`attempts` in `reservation_task:{id}`), then retries the success report with backoff for up to 10 min. Every web-side path that closes a silent reservation (`handle_process_exit`, `abort_interrupted`, `detect_lost_workers`, `expire_stale`) goes through `ReservationService._close_unreported`, which recovers that result (`launcher.recover_success`) as `success` before falling back to `error`. A late `success` report is also accepted over a system-set `error` (not over `cancelled`).

### Channels

- `ENABLE_TELEGRAM` (default true): Telegram bot + `/message` webhook. Requires bot token + webhook URL.
- `ENABLE_WEBAPP` (default false): `/api/*` + PWA at `/app/` (+ `/api/docs`). Requires HTTPS in production (service worker, push, `Secure` cookies).
- `/internal/events` (worker callbacks) is always mounted. Block `/internal` at the reverse proxy if possible.

### Environment Configurations

#### Local Execution (Port 8390)
- **Bot Token**: `BOTTOKEN_DEV` (development bot)
- **Webhook URL**: `WEBHOOK_URL_DEV`
- **Environment**: `IS_DEV=true` automatically set by Makefile (cookies are not `Secure`)
- **Purpose**: Local development and testing
- **Commands**: `make dev` (subprocess) / `make dev-mq` (Celery/MQ) / `make webapp-dev` (PWA dev server on 5173, proxies `/api` to 8390)

#### Docker/Production (Port 8391)
- **Bot Token**: `BOTTOKEN` (production bot)
- **Webhook URL**: `WEBHOOK_URL`
- **Environment**: `IS_DEV` not set (defaults to production)
- **Purpose**: Production deployment via Docker containers
- **Commands**: `make docker-compose-up` / `make docker-compose-up-mq`

### Key Components

#### Application Assembly
- **src/app.py**: Builds `Services`, the optional `TelegramBot`, and the FastAPI app (`web/factory.py::create_app`)
- **src/web/factory.py**: Lifespan (DB `migrate()` + ALLOW_LIST seed, housekeeping loop, webhook), router mounting
- **src/config.py**: Environment-based settings (`WebSettings`, `CelerySettings`)

#### Core Domain (`src/core/`)
- **egress.py**: Korail request egresses (`KORAIL_EGRESSES`, proxies or direct). `EgressPool.for_account` pins each Korail account to one egress (rendezvous hash, not stored in the DB); gates share per-egress pacing (`KORAIL_EGRESS_RPM`) and block backoff (5→15→30→60 min after code -2000): `FileGate` (subprocess, `logs/egress_{id}.json`), `RedisGate` (Celery, `egress_*` keys), `MemoryGate`. A blocked egress pauses; reservations never fail over to another egress (one account from many IPs is itself a macro signal)
- **reservations.py** `ReservationService`: start / cancel / list / worker events / stale expiry / 30-day purge
  - Issues `reservation_id` (uuid hex) and a per-reservation callback token (only its SHA-256 is stored)
  - Limits: `MAX_CONCURRENT_RESERVATIONS` (global), `MAX_RESERVATIONS_PER_USER` (admin exempt), `KORAIL_EGRESS_MAX_ACTIVE` (per egress, applies to admin too)
  - Spec carries `egress_id`, `egress_proxy` (CeleryLauncher encrypts it to `egress_proxy_enc`; a task that cannot decrypt it reports `error` instead of going direct), `egress_rpm`
- **launchers.py**: `SubprocessLauncher` (forkserver `Process`, sentinel wait thread → `handle_process_exit`; `start()` and exit-code reads share one lock because forkserver exit codes can be read from the pipe only once; cancel = SIGTERM to that PID only, never `killpg` - workers share the web server's process group), `CeleryLauncher` (encrypts Korail password for the broker), `create_launcher()` (falls back to subprocess if Redis is unreachable)
- **runner.py**: Worker-side loop shared by subprocess and Celery (`CallbackReporter` → `/internal/events`). Must not import DB/web modules.
- **auth.py** `AuthService`: web login (DB user check → throttle → Korail login), admin login, server-side sessions (token hash in DB, Fernet-encrypted Korail password), CSRF token
- **users.py** `UserService`: user DB (replaces `ALLOW_LIST`), Telegram chat linking
- **notifier.py**: `Notifier` fan-out, `SSEBroker`, `WebPushChannel` (`TelegramBot.deliver` is also a channel)
- **models.py / schemas.py / db.py / crypto.py / errors.py / vapid.py**
- **src/version.py**: Internal version (`VERSION = "vX.Y"`) - single source for the bot start message and the Docker image tag (`Makefile` reads it)

#### Web API (`src/web/`)
- `routes_auth.py` (`/api/auth/*`), `routes_reservations.py`, `routes_stations.py` (cached 공공데이터 search), `routes_events.py` (SSE), `routes_push.py`, `routes_admin.py` (user management), `routes_internal.py` (`/internal/events`), `static.py` (`/app/*` SPA fallback + cache headers)
- `deps.py`: `korail_session` cookie (HttpOnly, SameSite=Lax), `X-CSRF-Token` required on non-GET; `client_ip()` (login throttling) uses the peer address only - never read `X-Forwarded-For` directly. uvicorn (`fastapi run/dev`, proxy headers on by default) rewrites it only for `FORWARDED_ALLOW_IPS` (default 127.0.0.1; compose sets `172.16.0.0/12` for the Docker gateway)
- Errors are returned as `{"code": ..., "message": ...}` (`core/errors.py` → HTTP status)

#### Telegram Bot (`src/telegramBot/`)
- **bot.py**: Conversation state machine only (`userDict`); calls `ReservationService`; `deliver()` sends results
  - Admin-only commands (after ADMINPW login in that chat): `/users`, `/adduser`, `/deluser`, `/cancelall`, `/allusers`; `/status` shows all users only to admins (others see their own count)
- **tasks.py**: `reservation_task(spec)`; Redis `reservation_task:{task_id}` guard against duplicate execution; `Heartbeat` thread + `worker_shutting_down` handler (restart detection)
- **worker.py**: Subprocess entry point `run_process(spec, log_path)` (stdout/stderr → `logs/worker_{id8}.log`); `python -m telegramBot.worker` still reads a spec from stdin for manual runs
- **korail_client.py** (pykorail): `ReserveHandler.login` (bool + `loginError` reason) / `reserve_single_attempt` / `close`
  - `create_korail_client()`: logs Korail server block responses (code -2000)
  - `FATAL_ERRORS` (`StationNotFoundError`, `PastDepartureError`): `reserve_single_attempt` returns `fatal: True` → `core/runner.py` reports `failed` immediately
  - `create_korail_client(proxy_url, egress_id, *, device=)` sets the proxy on pykorail's curl_cffi session only and raises `KorailBlockedError` on code -2000; `reserve_single_attempt` returns `blocked: True`, `login()` sets `loginBlocked` → `core/runner.py` reports the block to the egress gate and waits (cancel / max duration checked every 5s, `progress` every 5 min so the 30-min stale rule does not fire)
  - Web/Telegram login checks use the account's egress (`core.auth.default_korail_login`); a blocked login raises `KorailUnavailable` (503) and is not counted as a password failure
  - **Waitlist (예약대기)** is opt-in per reservation: `ReservationRequest.allow_waitlist` (default false; forced false for `special_only`, see `WAITLIST_SEAT_TYPES`) → `reservations.allow_waitlist` (migration `0005`) → `spec["allow_waitlist"]` → `reserve_single_attempt(allowWaitlist=)`. Telegram asks it in state 13 (between seat type 10 and confirm 11, callbacks `waitlist_on/off`, skipped for `special_only`); the web form has a checkbox with a "예약대기란?" explanation. When enabled, search uses `include_waiting_list=True`. Each attempt first reserves a train with seats (departure order); only if every train in range is sold out does it register a waitlist on the earliest train with `has_waiting_list()` (no seats + flag 9) and return `waiting: True`. Korail's flag is general-class only, so the waitlist is always requested with `GENERAL_ONLY` (also for `special` = SPECIAL_FIRST) and `special_only` never waitlists. pykorail's `create()` registers a waitlist only for trains without any seat
- **messages.py**, **calendar_keyboard.py**, **time_keyboard.py**, **station_keyboard.py**

#### Web App (`webapp/`)
- Vite + React + TypeScript, TanStack Query, react-router (basename `/app`)
- `src/sw.ts`: Workbox precache (offline shell), push + notificationclick handlers (`injectManifest`)
- Screens: login (user/admin), home (active + 30-day history), new reservation, detail, settings (push/Telegram notify/install), admin (users, all reservations)
- Seat choice (`components/SeatPicker.tsx`): 일반실/특실 toggles + a priority choice when both are on; `format.ts` `seatSelectionFrom` / `toSeatType` map it to the same four `seat_type` values the API and Telegram use (both → `general`/`special`, one → `*_only`, none → blocked in the form)
- Dates/times are validated in **KST** on both client and server
- Icons: `npm run generate-icons` from `public/icon.svg`

### Docker Architecture

#### Profile-Based Service Management
- **`subprocess` Profile**: `web` only (SQLite at `./data`)
- **`celery` Profile**: `web_celery`, `redis`, `postgres`, `worker` (+ `flower`)

#### Container Configuration
- **Network**: Uses `korail_prod_network` bridge network
- **Port Mapping**: Port 8391 for production Docker deployment
- **Image**: Multi-stage — `node:22-alpine` builds `webapp/dist` → copied to `/app/webapp_dist`

#### Service Definitions
```yaml
# Subprocess Mode Services
web: FastAPI application (subprocess mode, SQLite volume ./data)

# MQ Mode Services
web_celery: FastAPI application (Celery mode), INTERNAL_CALLBACK_URL=http://web_celery:8391/internal/events
redis: Message broker and result backend (REQUIRED for MQ) - internal only (no host port), healthcheck gates web_celery/worker
postgres: Users / sessions / reservation history (REQUIRED for MQ web_celery) - internal only (no host port), healthcheck gates web_celery
worker: Celery worker (threads pool by default, --pool=${CELERY_POOL:-threads}) (REQUIRED for MQ)
flower: Web-based monitoring (OPTIONAL - for debugging)
```

**Important Notes:**
- **PostgreSQL**: Now used by `web_celery` (healthcheck-gated). Workers do not access the DB.
- **Beat**: Removed (2026-09-27). No periodic tasks are defined; housekeeping runs inside the web process. `docker-compose-up-mq` (`--remove-orphans`) removes an old `beat` container.
- **Flower**: Only needed during development/debugging to monitor MQ tasks.

### State Management Architecture

```python
# DB (SQLAlchemy, core/models.py) - both modes
users              # id = account_key (phone digits; admin Korail account may be email/membership no.), is_active, telegram_chat_id, telegram_notify, korail_device_profile, korail_android_id (unique)
web_sessions       # id = sha256(cookie token), encrypted Korail password, csrf_token, expires_at
reservations       # id = reservation_id, owner_id, origin, chat_id, status, runner_ref, attempts, allow_waitlist, waitlisted, ...
push_subscriptions # Web Push endpoints per user

# In-memory (bot.py) - Telegram conversation only
userDict = {}      # chat_id -> {inProgress, lastAction, userInfo{korailId, korailPw, ownerId, isAdmin}, trainInfo}
subscribes = []    # chats receiving broadcast notifications
```

Reservation status: `queued → running → success | failed | error | cancelled`.
A waitlist registration is `success` with `waitlisted=true` (worker reports `waiting: true`; also kept in the unreported-success result file / Redis state). Notifications and the web UI then say "예약대기 신청" instead of "20분 안에 결제" - check `waitlisted` wherever success is presented.
On startup, active `subprocess` reservations → `error` (their workers stopped with the previous server). Celery mode: lost-worker check every 60s (see Celery Mode). Housekeeping (every 10 min): RUNNING idle > 30 min or QUEUED > 24 h → `error`; terminal reservations older than `RESERVATION_RETENTION_DAYS` (default **30**) are deleted; expired sessions are deleted.

The DB schema is managed by Alembic (`src/core/migrations`, `alembic.ini` at the repo root). The web server runs `Database.migrate()` on startup:
- empty DB → `create_all` from the current models + stamp `head`
- pre-Alembic DB (tables but no `alembic_version`) → stamp baseline `0001`, then upgrade
- otherwise → upgrade to `head`

Any model change needs a revision in `src/core/migrations/versions/` (`DATABASE_URL=... pipenv run alembic revision --autogenerate -m "..."`, then review it; `alembic check` must report nothing). Keep fresh (`create_all`) and migrated schemas identical - e.g. declare indexes with `index=True` so names match `ix_<table>_<column>`. `env.py` uses `render_as_batch=True` for SQLite. Workers never touch the DB, so only the web server migrates.

### Multiple Reservation Support (Important!)

**Design Philosophy**: Each reservation is completely independent, identified by the `reservation_id` issued by `ReservationService`.

- `runner_ref` holds the PID (subprocess) or Celery task id (== `reservation_id`); it is only used for cancellation.
- Celery Redis guard key: `reservation_task:{task_id}` (task_id == reservation_id), status `running` (HSETNX) / `completed` / `cancelled`, with TTL; heartbeat `reservation_hb:{task_id}`
- Worker callbacks carry `reservation_id` + token → only that reservation is updated
- Same user can run several reservations at once, from Telegram and the web (per-user limit applies)
- Telegram cancel menu: callback data `cancel_{reservation_id}` or `cancel_all`
- Owner vs. chat: Telegram lists reservations where `owner_id == logged-in phone` OR `chat_id == this chat`; the web lists by `owner_id`; admin can use `scope=all`

**Never key reservation state by chat_id or user** — a second reservation would overwrite the first.

### Configuration-Based Initialization

```python
services = build_services(settings, use_celery)   # core/services.py
# use_celery=True  -> CeleryLauncher (Redis ping OK) else SubprocessLauncher
bot = TelegramBot(token, services)                 # if ENABLE_TELEGRAM
services.notifier.add(bot)
app = create_app(settings, services, bot)
```

**Important Note on Environment Variables:**
- The `.env` file should NOT set `USE_CELERY` to avoid conflicts
- Makefile commands use `PIPENV_DONT_LOAD_ENV=1` to prevent .env from overriding command-line settings
- This ensures `make dev` and `make run` always use subprocess mode
- And `make dev-mq` and `make run-mq` always use Celery mode (MQ pattern)
- `WEBAPP_ENC_KEY` must be identical for the web server and Celery workers (workers decrypt the password)

### Station Search Feature

Station names are selected via an API-driven search + selection flow (Telegram inline keyboard / web autocomplete), preventing typo-related reservation failures.

#### Data Source
- **API**: 공공데이터포털 (`apis.data.go.kr/B551457/run/v2/codes2`)
- **Search Method**: `cond[type::EQ]=stn_cd` + `cond[value::LIKE]={query}` (partial match)
- **Pagination**: `numOfRows=6`, navigated via inline keyboard buttons / "더 보기" on the web
- **Auth**: `DATAGOV_API_KEY` environment variable (서비스키)
- **Web**: `GET /api/stations?q=&page=` with a 24h in-process cache

#### Flow (Steps 5-6 in Conversation)
```
User types "광" → _input_src_station() → API search → inline keyboard:
  [광주열분] [광주송정]
  [광천]     [광운대]
  [대광리]
  [◀️ 이전] [1/5] [다음 ▶️]

User clicks "광주송정" → _select_src_station() → trainInfo["srcLocate"] = "광주송정"
User types new text → searches again (lastAction stays at 5 until selection)
```

#### Callback Data Formats
- Station selection: `station_src;역이름` or `station_dst;역이름`
- Pagination: `stn_page;station_src;검색어;페이지번호`

#### Key Implementation
- **`station_keyboard.py`**: `search_stations()` (async httpx call) + `create_station_keyboard()` (2-column layout)
- **`bot.py`**: `_input_src/dst_station()` triggers search, `_select_src/dst_station()` finalizes selection
- **Two-phase flow**: Text input = search (lastAction unchanged), button click = select (lastAction advances)
- **`webapp/src/components/StationInput.tsx`**: same rule — a station is confirmed only when chosen from results

### Conversation Flow Architecture (Telegram)

1. **User Authentication**: Phone number must be an active user in the DB
2. **Korail Login**: Account credential validation (links `telegram_chat_id` to the user)
3. **Interactive Selection**: date, stations, time range, train type (`KTX`/`ALL`), seat type (`general`/`general_only`/`special`/`special_only`), waitlist on/off (not asked for `special_only`)
4. **Reservation Execution**: `ReservationService.start(..., origin="telegram", chat_id=...)`
5. **Status Updates**: worker → `/internal/events` → `Notifier` (Telegram/SSE/Web Push)
6. **Completion Handling**: `TelegramBot.deliver()` sends the result and resets `userDict` if no reservation remains

The web app submits the same fields in one form (`ReservationRequest` validates both channels).

### Background Task Processing

#### Subprocess Flow
```
ReservationService -> SubprocessLauncher (forkserver Process, spec pickled over its socket) -> worker.run_process -> core/runner.py -> Korail API
                   <- POST /internal/events {reservation_id, token, status, ...}
```

#### MQ Flow
```
ReservationService -> CeleryLauncher (apply_async, task_id=reservation_id) -> Redis -> Celery worker
                   -> core/runner.py -> Korail API
                   <- POST INTERNAL_CALLBACK_URL {reservation_id, token, status, ...}
```

## Development Commands

### Setup and Installation
```bash
make setup-pipenv     # Install pipenv globally
make install          # Install dependencies with pipenv
```

### Local Development (Port 8390, IS_DEV=true, uses BOTTOKEN_DEV)
```bash
# Development - Subprocess mode (simple, single process)
make dev              # Run development server

# Development - Celery mode (MQ pattern) (distributed, multiple processes)
make dev-mq       # Starts Redis + Worker + Flower + Web (ALL-IN-ONE)
make dev-mq-stop  # Stop Worker + Flower (Redis stays running)
```

### Local Production (Port 8391, IS_DEV=false, uses BOTTOKEN)
```bash
# Production - Subprocess mode
make run              # Run production server

# Production - Celery mode (MQ pattern)
make run-mq       # Starts Redis + Worker + Flower + Web (ALL-IN-ONE)
make run-mq-stop  # Stop Worker + Flower (Redis stays running)
```

### Individual Service Management (Advanced)
```bash
make redis-start           # Start local Redis server
make redis-stop            # Stop local Redis server
make celery-worker-start   # Start Celery worker in background
make celery-worker-stop    # Stop Celery worker
make celery-flower-start   # Start Flower monitoring UI (http://localhost:5555)
make celery-flower-stop    # Stop Flower monitoring UI
```

### Docker Compose Operations (Production)
```bash
make docker-build              # Build Docker image
make docker-push               # Publish Docker image

make docker-compose-up         # Start subprocess mode
make docker-compose-up-mq  # Start Celery mode (MQ pattern) (RECOMMENDED for production)
make docker-compose-down       # Stop all services
# docker-compose-up(-mq) recreates only changed containers (up -d --build). It first removes only the services
# the target mode does not use (the other mode's services) to avoid port clashes, and
# fails fast if host port 8391/5555 is taken by something outside this compose project (e.g. a local server)
# They set BUILDX_NO_DEFAULT_ATTESTATIONS=1: default provenance attestations make every build a new image ID,
# which would recreate all app containers even without code changes (build.provenance in compose is ignored by compose v5)
make docker-compose-logs       # Show logs from running services

# Code Changes - IMPORTANT: Always use --build when code changes
docker compose down
docker compose --profile celery up -d --build     # Rebuild and start Celery mode (MQ pattern)
docker compose --profile subprocess up -d --build # Rebuild and start subprocess mode
```

### Web App (PWA)
```bash
make webapp-install   # npm ci in webapp/
make webapp-dev       # Vite dev server http://localhost:5173/app/ (proxies /api to 8390; run `make dev` too)
make webapp-build     # Build webapp/dist (served by FastAPI at /app when ENABLE_WEBAPP=true)
make vapid-keys       # Generate VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY for Web Push
```

### Code Quality
```bash
make lint             # Format code with black (CI pins black 25.9.0)
make test             # All Python tests (TEST_DATABASE_URL=postgresql://... to run DB tests on Postgres)
cd webapp && npm run build   # Typecheck (tsc) + build
```

### Important Notes About Local MQ Mode

When running `make dev-mq` or `make run-mq`:
1. **Redis** starts automatically (local daemon process)
2. **Celery worker** starts automatically in background
3. **Flower UI** starts automatically in background (port 5555)
4. **FastAPI web** starts in foreground (you'll see logs)
5. Press `Ctrl+C` to stop web server
6. **IMPORTANT**: Run `make dev-mq-stop` or `make run-mq-stop` to cleanup

**Cleanup behavior:**
- `make dev-mq-stop` or `make run-mq-stop` stops **Worker + Flower only**
- **Redis stays running** for faster subsequent startups
- To stop Redis manually: `make redis-stop`

If you forget to run the stop command:
- **Redis** will keep running (can be stopped with `make redis-stop`)
- **Celery worker** will keep running in background
- **Flower** will keep running in background
- Run the cleanup command to stop worker and Flower

### Prerequisites for Local MQ Mode

You must have Redis installed on your system:
```bash
# Ubuntu/Debian
sudo apt install redis-server

# macOS
brew install redis
```

**Redis Management:**
- Docker Compose Redis is not published to the host, so local Redis (localhost:6379) and Docker can run side by side
- `make redis-start` / `redis-stop` detect local Redis with `redis-cli ping` (not `pgrep`, which also matches Redis inside containers)
- Local `make dev/run(-mq)` check that their web port (8390/8391) is free first; local Flower is skipped if 5555 is taken
- Use `make redis-start` to start Redis (runs as daemon process)
- Use `make redis-stop` to stop Redis when needed
- Redis will automatically start when running `make dev-mq` or `make run-mq`
- Redis persists between development sessions for faster startups (stop manually if needed)

### Command Summary

| Command | Mode | Port | Bot Token | Services Started |
|---------|------|------|-----------|------------------|
| `make dev` | Development | 8390 | DEV | Web only (subprocess) |
| `make dev-mq` | Development | 8390 | DEV | Redis + Worker + Flower + Web |
| `make run` | Production | 8391 | PRODUCTION | Web only (subprocess) |
| `make run-mq` | Production | 8391 | PRODUCTION | Redis + Worker + Flower + Web |
| `make docker-compose-up-mq` | Production | 8391 | PRODUCTION | All services in Docker |
| `make webapp-dev` | Development | 5173 | - | PWA dev server (API proxied to 8390) |

## Environment Variables

### Required Variables for Local Execution
```bash
BOTTOKEN_DEV          # Development Telegram bot token (for local execution)
WEBHOOK_URL_DEV       # Development webhook URL (for local execution)
ALLOW_LIST            # Phone numbers seeded into the user DB on startup (DB is the source of truth afterwards)
                      # ADMIN_KORAIL_ID is also added on startup as an active row (device identity storage)
ADMINPW               # Admin password for privileged access (Telegram + web admin login)
```

### Channels / Web App Variables
```bash
ENABLE_TELEGRAM       # true (default) / false
ENABLE_WEBAPP         # false (default) / true
DATABASE_URL          # default sqlite:///./korail_bot.db (postgresql://... is mapped to psycopg)
WEBAPP_ENC_KEY        # Korail password encryption secret (REQUIRED in production; same value for workers)
SESSION_TTL_HOURS     # "로그인 유지" session lifetime, default 168
COOKIE_SECURE         # override Secure cookie flag (default: not IS_DEV)
FORWARDED_ALLOW_IPS   # proxies whose X-Forwarded-For is trusted (uvicorn; default 127.0.0.1, compose 172.16.0.0/12)
WEBAPP_ORIGIN         # only if the PWA is served from another origin (enables CORS for it)
INTERNAL_CALLBACK_URL # worker -> web callback (default http://127.0.0.1:{8390|8391}/internal/events)
VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT   # Web Push (make vapid-keys)
MAX_RESERVATIONS_PER_USER   # default 3 (admin exempt)
RESERVATION_TIMEOUT         # max seconds one reservation keeps trying (both modes), default 3600
RESERVATION_RETENTION_DAYS  # default 30
KORAIL_EGRESSES             # Korail egresses "id=proxy_url,..." (id alone = direct), default: one direct egress
KORAIL_EGRESS_RPM           # searches per minute per egress (all its reservations), default 60, 0 = unlimited
KORAIL_EGRESS_MAX_ACTIVE    # concurrent reservations per egress, default 0 = unlimited
LOGIN_MAX_FAILURES / LOGIN_LOCK_MINUTES   # default 3 per 10 min (Korail locks after 5)
```

### Production Variables (Docker Environment)
```bash
BOTTOKEN              # Production Telegram bot token
WEBHOOK_URL           # Production webhook URL
```

### MQ Mode Variables
```bash
USE_CELERY            # Enable Celery mode (MQ pattern) (true/false)
REDIS_URL             # Redis connection URL
CELERY_BROKER         # MQ broker URL
CELERY_RESULT_BACKEND # MQ result backend URL
CELERY_POOL           # threads (default) / prefork - web server and workers must agree (cancel method)
CELERY_CONCURRENCY    # worker slots, default MAX_CONCURRENT_RESERVATIONS
```

### Station Search API
```bash
DATAGOV_API_KEY       # 공공데이터포털 API 서비스키 (역 검색용)
```

### Korail Client (pykorail) Notes
- pykorail is pinned (`==0.2.1`): `create_korail_client()` and `scripts/check_korail_login.py` wrap its private `client._api._parse`
- Since 0.2.1, a non-Korail HTTP 4xx/5xx body (e.g. the 403 `-2000` block) raises `HttpStatusError` (a `TransportError`, not `KorailError`) from `_parse`; `ReserveHandler.loginError` shows it as "코레일 서버가 요청을 거절했습니다: ...", not as a password error
- Since 0.2.1, every `Korail()` without `device_profile`/`android_id` signs with a freshly generated synthetic Android ID (0.2.0 used one fixed ID for everyone) and the User-Agent is always `korailtalk`
- **Device identity (Android ID)**: Korail blocks accounts that share an Android ID, and a new ID on every login looks like a new phone. Each Korail account therefore uses one fixed device `{"profile_id", "android_id"}` from `UserService.korail_device(korail_id)`:
  - issued on first use (`random_profile()`) and stored in `users.korail_device_profile` / `users.korail_android_id` (unique; written only while NULL so concurrent first logins agree)
  - the admin Korail account (`ADMIN_KORAIL_ID`) also gets a `users` row (`ensure_admin_account()`, on startup and whenever its device is needed, so a deleted row is re-added with a new device). It is added active (name "관리자 코레일 계정"), so a phone-number admin account can also log in as a regular user (per-user limit applies there); an existing row (e.g. the admin's phone is a registered user) is left as is
  - `users.id` is `core.schemas.account_key(korail_id)`: phone → digits, email → lowercase, other ids (membership number) → stripped (up to 50 chars, migration `0003`). `UserService` lookups use `account_key`
  - an account without a row gets `None` (pykorail makes a one-off ID) and a warning is logged
  - used by every login: web/admin login (`AuthService._korail_login`), Telegram login (`default_korail_login(..., device=)` → `ReserveHandler(..., device=)`), and workers (`ReservationService` puts it in `spec["korail_device"]`; `run_reservation` builds `ReserveHandler(proxy_url, egress_id, device=)`, so re-logins keep the same egress and device)
  - never call `create_korail_client()` / `ReserveHandler()` without the device in production code
- Phone numbers are accepted with or without hyphens (also spaces/dots) everywhere (`is_valid_phone`, `PHONE_FORMAT_HINT`); user ids are stored as digits, and Korail receives `010-1234-5678` (pykorail 0.2.1 strips the hyphens and sends the phone login flag)
- `login()` raises `LoginFailedError` instead of returning `False`; `ReserveHandler.login()` still returns a bool and keeps a user-facing reason in `ReserveHandler.loginError`
- Korail server block (anti-macro) responses look like `{"code": -2000, "id", "message"}`; pykorail drops them (login fails / search looks like "no trains"), so `create_korail_client()` wraps the response parser, logs them at ERROR (`코레일 서버 차단 응답 ...`, URL without query string, egress id) and raises `KorailBlockedError` (also when pykorail 0.2.1 already raised `HttpStatusError` for the 403 block body). Cloudflare WARP egress (container and host proxy mode) was blocked with -2000 while direct egress worked, so WARP support was removed; use proxies on lines you own (`KORAIL_EGRESSES`)
- pykorail's fallback "아이디 또는 비밀번호가 올바르지 않습니다" (code `None`) means the server sent no reason - real wrong-password responses carry a code such as `WRR000101`
- `make korail-login-check` pipes `scripts/check_korail_login.py` into the running web container to diagnose ADMIN_KORAIL_ID/PW (env values as received, raw server response)
- Station names are validated against Korail's station master before searching (`StationNotFoundError`)
- Searching a past time raises `PastDepartureError`; `ReserveHandler._depart_after()` clamps today's past times to now (KST)
- `TrainType` / `ReserveOption` are plain string constants (same values as korail2), safe for the worker spec (subprocess forkserver / Celery JSON)

### Optional Variables
```bash
ADMIN_KORAIL_ID       # Default Korail username for admin quick-login
ADMIN_KORAIL_PW       # Default Korail password for admin quick-login
```

## Testing and Quality Assurance

### Manual Testing Focus Areas
- **Complete reservation flow**: End-to-end user journey testing
- **Mode switching**: Verify subprocess and Celery mode (MQ pattern) functionality
- **Error handling**: Network failures, invalid credentials, API changes
- **Process management**: Background task lifecycle and cleanup
- **Environment isolation**: Dev and prod environment separation

### Performance Monitoring
- **Subprocess mode**: Monitor process spawning and memory usage
- **Celery mode (MQ pattern)**: Use Flower for task monitoring and worker health
- **Resource usage**: Monitor container resource consumption
- **Response times**: Webhook response latency and task completion times

### Critical Testing Scenarios
1. **Authentication flow**: Phone number verification and Korail login (Telegram + web, throttling)
2. **Reservation process**: Date/time selection and background execution
3. **Error recovery**: Network failures and session timeouts
4. **Concurrent usage**: Multiple users in Celery mode (MQ pattern)
5. **Environment switching**: Dev to prod deployment verification
6. **PWA**: install, offline shell, push permission after first reservation, update prompt

## Architecture Decisions and Rationales

### Why Dual Mode Architecture?
- **Subprocess Mode**: Simplifies deployment for single users
- **MQ Mode**: Enables scalability for multi-user scenarios
- **Configuration-based**: Clean separation without code duplication

### Capacity (measured 2026-09-26, see `docs/capacity-review.md`)
- Subprocess mode is memory-bound: ~8MB PSS per concurrent reservation since the forkserver switch (2026-09-27; was ~22MB); CPU ~0.04% of a core each; the web server is not a bottleneck
- Celery prefork pre-allocates every slot (~35MB each); Celery threads (default since 2026-09-27) is ~0.3MB per reservation and cancels cooperatively via Redis (stops within one attempt interval)
- The practical ceiling for both modes is Korail's per-IP request limit, not server resources
- Keep worker imports light: `telegramBot/__init__.py` must not import `bot.py`

### Why Docker Profiles?
- **Resource optimization**: Only run necessary services
- **Environment isolation**: Prevent port conflicts and resource contention
- **Simplified deployment**: Single command deployment for different modes

### Why a Channel-Agnostic Core?
- Telegram and the web app share one reservation implementation (`core/`), so limits, validation, callbacks and history behave identically
- Adding a channel means adding an adapter + a `Notifier` channel, not touching workers

### Why a Database (SQLite / PostgreSQL)?
- Users are managed at runtime (admin screen, `/adduser`) instead of redeploying `ALLOW_LIST`
- Web sessions and reservation history (30 days) must survive restarts
- Subprocess mode stays lightweight with SQLite; Celery mode uses PostgreSQL from the compose stack

### Why Server Sessions (not JWT)?
- The Korail password must be kept (encrypted) server-side for workers to re-login
- Immediate revocation on logout / user deactivation

Always run `make lint` before committing changes to maintain code formatting consistency.

## Service Optimization Recommendations

### MQ Profile Services
```yaml
Services needed: web_celery, redis, postgres, worker
Optional: flower (for debugging only; published on 5555 without auth - keep it off public networks)
Removed: beat (no periodic tasks; add it back only if a beat_schedule is introduced)
```

## Important Notes for AI Assistant

1. **Mode Selection**: Always consider whether changes affect subprocess mode, Celery mode (MQ pattern), or both
2. **Environment Awareness**: Be mindful of local (IS_DEV=true) vs Docker/production configurations
3. **Docker Profiles**: Remember to use appropriate profiles when testing Docker setups
4. **State Management**: Understand the different storage mechanisms for each mode
5. **Configuration Over Detection**: Use configuration parameters instead of ImportError patterns
6. **Resource Considerations**: Subprocess mode should remain lightweight, Celery mode (MQ pattern) can use more resources
7. **Reservation identity**: Always use `reservation_id`; never key reservation state by chat_id/user. Workers must only talk to the web server via `/internal/events`.
8. **Secrets**: Never pass the Korail password via argv or log it; subprocess gets it via the forkserver socket (process args), Celery gets `korail_pw_enc`.
9. **Both channels**: Changes to reservation rules belong in `core/` (and `ReservationRequest` validation), not in `bot.py` or `web/`.
10. **Docker Build Strategy**: **CRITICAL** - When code changes, ALWAYS use `docker compose up -d --build` to ensure containers get updated code. All services use `build: .` context and share the same codebase. Never manually rebuild individual services or use complex docker build/tag workflows. The correct process is:
   ```bash
   docker compose down
   docker compose --profile celery up -d --build     # For Celery mode (MQ pattern)
   docker compose --profile subprocess up -d --build # For subprocess mode
   ```
