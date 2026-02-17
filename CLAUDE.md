# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

This is a Telegram bot for KTX (Korean train) reservation automation built with a dual-mode architecture supporting both lightweight subprocess execution and scalable distributed processing.

### Technology Stack

- **FastAPI**: Modern web framework for webhook-based Telegram bot backend
- **python-telegram-bot**: Comprehensive library for Telegram Bot API interactions
- **korail2**: KTX reservation API client library
- **Redis + Celery**: Optional distributed task processing system
- **PostgreSQL**: Optional persistent data storage for Celery mode
- **Docker**: Complete containerization with multi-environment support

### Execution Modes

#### Subprocess Mode (Default/Lightweight)
- **Purpose**: Single-user deployments, development, testing
- **Storage**: In-memory Python dictionaries (`userDict`, `runningStatus`, `subscribes`)
- **Background Tasks**: Python subprocess execution via `telegramBot.worker`
- **Dependencies**: Minimal - only FastAPI web server
- **Resource Usage**: Low memory and CPU footprint
- **Scaling**: Vertical scaling only (single server)

#### Celery Mode (Distributed/Scalable)
- **Purpose**: Multi-user deployments, production environments
- **Storage**: Redis for state management and task queuing
- **Background Tasks**: Distributed Celery workers with task monitoring
- **Dependencies**: Redis (broker), PostgreSQL (optional persistence), Celery workers
- **Resource Usage**: Higher but horizontally scalable
- **Scaling**: Horizontal scaling across multiple servers

### Environment Configurations

#### Local Execution (Port 8390)
- **Bot Token**: `BOTTOKEN_DEV` (development bot)
- **Webhook URL**: `WEBHOOK_URL_DEV`
- **Environment**: `IS_DEV=true` automatically set by Makefile
- **Purpose**: Local development and testing
- **Commands**: `make dev` (subprocess) / `make dev-celery` (Celery)

#### Docker/Production (Port 8391)
- **Bot Token**: `BOTTOKEN` (production bot)
- **Webhook URL**: `WEBHOOK_URL`
- **Environment**: `IS_DEV` not set (defaults to production)
- **Purpose**: Production deployment via Docker containers
- **Commands**: `make docker-compose-up` / `make docker-compose-up-celery`

### Key Components

#### Core Application Files
- **src/app.py**: FastAPI server entry point
  - Webhook setup and lifecycle management
  - Health check endpoints
  - Reservation completion callbacks
  - Celery task result handling

- **src/config.py**: Configuration management
  - Environment-based settings
  - Redis/Celery connection parameters
  - Bot token and webhook URL selection

#### Bot Logic
- **src/telegramBot/bot.py**: Main bot implementation
  - **Dual-mode initialization**: Configuration-based Redis/Celery setup
  - **State management**: User conversation flow and reservation tracking
  - **Command handlers**: `/start`, `/cancel`, `/status`, admin commands
  - **Callback handlers**: Interactive keyboard responses
  - **Process management**: Subprocess and Celery task orchestration

- **src/telegramBot/tasks.py**: Celery task definitions
  - **reservation_task**: Distributed reservation processing
  - **Callback system**: HTTP status updates to main application
  - **Error handling**: Automatic retry logic and failure notifications

- **src/telegramBot/worker.py**: Subprocess worker implementation
  - **Standalone process**: Isolated reservation execution
  - **Korail API integration**: Direct API calls with session management
  - **Callback system**: HTTP status updates via completion endpoint

#### Supporting Modules
- **src/telegramBot/korail_client.py**: Korail API client wrapper
- **src/telegramBot/messages.py**: Centralized message templates
- **src/telegramBot/calendar_keyboard.py**: Interactive date selection interface
- **src/telegramBot/time_keyboard.py**: Time preference selection interface
- **src/telegramBot/station_keyboard.py**: Station search and selection interface (공공데이터포털 API)

### Docker Architecture

#### Profile-Based Service Management
- **No Profile**: Base services only
- **`subprocess` Profile**: Lightweight web service only
- **`celery` Profile**: Full stack with Redis, PostgreSQL, workers

#### Container Configuration
- **Network**: Uses `korail_prod_network` bridge network
- **Port Mapping**: Port 8391 for production Docker deployment
- **Note**: Local execution uses port 8390 (non-Docker)

#### Service Definitions
```yaml
# Subprocess Mode Services
web: FastAPI application (subprocess mode)

# Celery Mode Services
web_celery: FastAPI application (Celery mode)
redis: Message broker and state storage (REQUIRED for Celery)
postgres: Persistent database (CURRENTLY UNUSED - reserved for future use)
worker: Celery worker processes (REQUIRED for Celery)
beat: Celery scheduler (NOT NEEDED - no periodic tasks defined)
flower: Web-based monitoring (OPTIONAL - for debugging)
```

**Important Notes:**
- **PostgreSQL**: Currently not used in the codebase. Celery uses Redis for both broker and result backend. Can be removed to save resources or kept for future development.
- **Beat**: No periodic tasks (`@periodic_task`) are defined, so this service is unused. Can be removed.
- **Flower**: Only needed during development/debugging to monitor Celery tasks.

### State Management Architecture

#### Subprocess Mode State
```python
# In-memory storage (bot.py)
userDict = {}        # User conversation state and reservation details
runningStatus = {}   # Active reservation processes
                     # Key: PID (subprocess mode) or task_id (Celery mode)
                     # Value: {chat_id, task_id/pid, korailId, method}
subscribes = []      # Users receiving broadcast notifications
```

#### Celery Mode State
```python
# Redis-based storage
redis_client: Redis connection for state persistence
celery_app: Celery application for task management
# PostgreSQL for optional long-term data persistence

# Task-based isolation
# Each reservation uses Celery task_id as unique identifier
# Redis key format: reservation_task:{task_id}
# This allows same user to have multiple concurrent reservations
```

### Multiple Reservation Support (Important!)

**Design Philosophy**: Each reservation task is completely independent, identified by its unique task_id (Celery) or PID (subprocess).

#### Key Implementation Details

1. **Task Isolation**
   - **Subprocess Mode**: Uses process PID as unique identifier
   - **Celery Mode**: Uses Celery task_id (`self.request.id`) as unique identifier
   - Each reservation maintains independent state in `runningStatus[task_id]`

2. **Redis Key Structure (Celery Mode)**
   ```python
   # tasks.py line 54
   reservation_key = f"reservation_task:{self.request.id}"

   # This allows:
   # - Same user, multiple different reservations (different routes)
   # - Same user, same route, multiple attempts (retry after failure)
   # - Complete task independence
   ```

3. **runningStatus Structure**
   ```python
   # bot.py line 682-687
   self.runningStatus[task_id] = {
       "chat_id": chat_id,      # User's Telegram chat ID
       "task_id": task_id,      # Celery task ID or subprocess PID
       "korailId": user_info["korailId"],  # Korail account
       "method": "celery",      # "celery" or "subprocess"
   }
   ```

4. **Cancel Menu Interface**
   - Shows list of ongoing reservations for the user
   - User can select specific reservation to cancel
   - Or cancel all reservations at once
   - Implementation: `_show_cancel_menu()` at bot.py:832-874

5. **Callback Handling**
   - All callbacks include `task_id` parameter
   - App identifies and cleans up specific task: `app.py:163`
   - Preserves other concurrent reservations for same user

#### Why task_id Instead of chat_id?

**Problem with chat_id**: Would only allow one reservation per user
```python
# ❌ OLD (wrong):
runningStatus[chat_id] = {...}  # Second reservation overwrites first!
```

**Solution with task_id**: Allows unlimited concurrent reservations
```python
# ✅ NEW (correct):
runningStatus[task_id_1] = {chat_id: 123, ...}  # First reservation
runningStatus[task_id_2] = {chat_id: 123, ...}  # Second reservation (same user!)
```

### Configuration-Based Initialization

The bot uses configuration-based service initialization instead of ImportError checking:

```python
def __init__(self, token: str, enable_redis_celery: bool = False):
    self.use_celery = enable_redis_celery
    if self.use_celery and REDIS_AVAILABLE:
        # Initialize Redis and Celery
    else:
        # Use in-memory storage and subprocess execution
```

**Important Note on Environment Variables:**
- The `.env` file should NOT set `USE_CELERY` to avoid conflicts
- Makefile commands use `PIPENV_DONT_LOAD_ENV=1` to prevent .env from overriding command-line settings
- This ensures `make dev` and `make run` always use subprocess mode
- And `make dev-celery` and `make run-celery` always use Celery mode

### Station Search Feature

Station names are selected via an API-driven search + inline keyboard flow, preventing typo-related reservation failures.

#### Data Source
- **API**: 공공데이터포털 (`apis.data.go.kr/B551457/run/v2/codes2`)
- **Search Method**: `cond[type::EQ]=stn_cd` + `cond[value::LIKE]={query}` (partial match)
- **Pagination**: `numOfRows=5`, navigated via inline keyboard buttons
- **Auth**: `DATAGOV_API_KEY` environment variable (서비스키)

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

### Conversation Flow Architecture

1. **User Authentication**: Phone number verification against `ALLOW_LIST`
2. **Korail Login**: Account credential validation
3. **Interactive Selection**:
   - Date selection via calendar keyboard
   - Station search via 공공데이터포털 API + inline keyboard selection
   - Time preferences and train type selection
   - Seat type preferences
4. **Reservation Execution**: Mode-specific background processing
5. **Status Updates**: Webhook-based real-time notifications
6. **Completion Handling**: State cleanup and user notifications

### Background Task Processing

#### Subprocess Flow
```
Bot -> subprocess.Popen() -> worker.py -> Korail API -> HTTP callback -> Bot
```

#### Celery Flow  
```
Bot -> Celery task -> Redis queue -> Worker process -> HTTP callback -> Bot
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

# Development - Celery mode (distributed, multiple processes)
make dev-celery       # Starts Redis + Worker + Flower + Web (ALL-IN-ONE)
make dev-celery-stop  # Stop Worker + Flower (Redis stays running)
```

### Local Production (Port 8391, IS_DEV=false, uses BOTTOKEN)
```bash
# Production - Subprocess mode
make run              # Run production server

# Production - Celery mode
make run-celery       # Starts Redis + Worker + Flower + Web (ALL-IN-ONE)
make run-celery-stop  # Stop Worker + Flower (Redis stays running)
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
make docker-compose-up-celery  # Start Celery mode (RECOMMENDED for production)
make docker-compose-down       # Stop all services
make docker-compose-logs       # Show logs from running services

# Code Changes - IMPORTANT: Always use --build when code changes
docker compose down
docker compose --profile celery up -d --build     # Rebuild and start Celery mode
docker compose --profile subprocess up -d --build # Rebuild and start subprocess mode
```

### Code Quality
```bash
make lint             # Format code with black
```

### Important Notes About Local Celery Mode

When running `make dev-celery` or `make run-celery`:
1. **Redis** starts automatically (local daemon process)
2. **Celery worker** starts automatically in background
3. **Flower UI** starts automatically in background (port 5555)
4. **FastAPI web** starts in foreground (you'll see logs)
5. Press `Ctrl+C` to stop web server
6. **IMPORTANT**: Run `make dev-celery-stop` or `make run-celery-stop` to cleanup

**Cleanup behavior:**
- `make dev-celery-stop` or `make run-celery-stop` stops **Worker + Flower only**
- **Redis stays running** for faster subsequent startups
- To stop Redis manually: `make redis-stop`

If you forget to run the stop command:
- **Redis** will keep running (can be stopped with `make redis-stop`)
- **Celery worker** will keep running in background
- **Flower** will keep running in background
- Run the cleanup command to stop worker and Flower

### Prerequisites for Local Celery Mode

You must have Redis installed on your system:
```bash
# Ubuntu/Debian
sudo apt install redis-server

# macOS
brew install redis
```

**Redis Management:**
- Use `make redis-start` to start Redis (runs as daemon process)
- Use `make redis-stop` to stop Redis when needed
- Redis will automatically start when running `make dev-celery` or `make run-celery`
- Redis persists between development sessions for faster startups (stop manually if needed)

### Command Summary

| Command | Mode | Port | Bot Token | Services Started |
|---------|------|------|-----------|------------------|
| `make dev` | Development | 8390 | DEV | Web only (subprocess) |
| `make dev-celery` | Development | 8390 | DEV | Redis + Worker + Flower + Web |
| `make run` | Production | 8391 | PRODUCTION | Web only (subprocess) |
| `make run-celery` | Production | 8391 | PRODUCTION | Redis + Worker + Flower + Web |
| `make docker-compose-up-celery` | Production | 8391 | PRODUCTION | All services in Docker |

## Environment Variables

### Required Variables for Local Execution
```bash
BOTTOKEN_DEV          # Development Telegram bot token (for local execution)
WEBHOOK_URL_DEV       # Development webhook URL (for local execution)
ALLOW_LIST            # Comma-separated phone numbers
ADMINPW               # Admin password for privileged access
```

### Production Variables (Docker Environment)
```bash
BOTTOKEN              # Production Telegram bot token
WEBHOOK_URL           # Production webhook URL
```

### Celery Mode Variables
```bash
USE_CELERY            # Enable Celery mode (true/false)
REDIS_URL             # Redis connection URL
CELERY_BROKER         # Celery broker URL
CELERY_RESULT_BACKEND # Celery result backend URL
```

### Station Search API
```bash
DATAGOV_API_KEY       # 공공데이터포털 API 서비스키 (역 검색용)
```

### Optional Variables
```bash
ADMIN_KORAIL_ID       # Default Korail username for admin quick-login
ADMIN_KORAIL_PW       # Default Korail password for admin quick-login
```

## Testing and Quality Assurance

### Manual Testing Focus Areas
- **Complete reservation flow**: End-to-end user journey testing
- **Mode switching**: Verify subprocess and Celery mode functionality
- **Error handling**: Network failures, invalid credentials, API changes
- **Process management**: Background task lifecycle and cleanup
- **Environment isolation**: Dev and prod environment separation

### Performance Monitoring
- **Subprocess mode**: Monitor process spawning and memory usage
- **Celery mode**: Use Flower for task monitoring and worker health
- **Resource usage**: Monitor container resource consumption
- **Response times**: Webhook response latency and task completion times

### Critical Testing Scenarios
1. **Authentication flow**: Phone number verification and Korail login
2. **Reservation process**: Date/time selection and background execution
3. **Error recovery**: Network failures and session timeouts
4. **Concurrent usage**: Multiple users in Celery mode
5. **Environment switching**: Dev to prod deployment verification

## Architecture Decisions and Rationales

### Why Dual Mode Architecture?
- **Subprocess Mode**: Simplifies deployment for single users
- **Celery Mode**: Enables scalability for multi-user scenarios
- **Configuration-based**: Clean separation without code duplication

### Why Docker Profiles?
- **Resource optimization**: Only run necessary services
- **Environment isolation**: Prevent port conflicts and resource contention
- **Simplified deployment**: Single command deployment for different modes

### Why In-Memory vs Redis Storage?
- **Performance**: In-memory access is faster for single-user scenarios
- **Simplicity**: Reduces infrastructure complexity for basic deployments
- **Persistence**: Redis provides durability for production environments

Always run `make lint` before committing changes to maintain code formatting consistency.

## Service Optimization Recommendations

### Services That Can Be Removed (Celery Profile)
1. **PostgreSQL (`postgres`)**: Not used anywhere in the codebase. Celery uses Redis for results.
   - Remove to save ~50MB RAM and disk space
   - Keep if planning to add persistent data storage in future

2. **Beat (`beat`)**: No periodic tasks defined in the application.
   - Remove to save ~100MB RAM
   - Only add back if implementing scheduled tasks

### Minimal Celery Profile (Recommended)
```yaml
Services needed: web_celery, redis, worker
Optional: flower (for debugging only)
Remove: postgres, beat
```

## Important Notes for AI Assistant

1. **Mode Selection**: Always consider whether changes affect subprocess mode, Celery mode, or both
2. **Environment Awareness**: Be mindful of local (IS_DEV=true) vs Docker/production configurations
3. **Docker Profiles**: Remember to use appropriate profiles when testing Docker setups
4. **State Management**: Understand the different storage mechanisms for each mode
5. **Configuration Over Detection**: Use configuration parameters instead of ImportError patterns
6. **Resource Considerations**: Subprocess mode should remain lightweight, Celery mode can use more resources
7. **PostgreSQL**: Currently unused - Celery uses Redis for all storage needs
7. **Docker Build Strategy**: **CRITICAL** - When code changes, ALWAYS use `docker compose up -d --build` to ensure containers get updated code. All services use `build: .` context and share the same codebase. Never manually rebuild individual services or use complex docker build/tag workflows. The correct process is:
   ```bash
   docker compose down
   docker compose --profile celery up -d --build     # For Celery mode
   docker compose --profile subprocess up -d --build # For subprocess mode
   ```