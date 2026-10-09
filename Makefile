-include .env
export

VERSION := $(shell sed -n 's/^VERSION = "\(.*\)"/\1/p' src/version.py)
IMAGE_NAME := raonsol/korail_telegrambot:$(VERSION)

WORKER_PID_FILE := .celery-worker.pid
FLOWER_PID_FILE := .celery-flower.pid

# 호스트 포트가 사용 중인지 확인 (사용 중이면 0)
PORT_IN_USE = python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', $(1))) == 0 else 1)"

# 포트가 비어 있어야 하는 대상의 선행 조건 (예: port-free-8391)
port-free-%:
	@if $(call PORT_IN_USE,$*); then \
		echo "❌ 포트 $* 이(가) 이미 사용 중입니다."; \
		echo "   Docker Compose 가 떠 있다면 'make docker-compose-down', 로컬 서버(make run 등)라면 해당 프로세스를 먼저 종료하세요."; \
		exit 1; \
	fi

.PHONY: help
help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: setup-uv
setup-uv:  ## Install uv (Python package manager) for the current user
	pip install --user uv --break-system-packages

.PHONY: install
install:	## Install dependencies (incl. dev) into .venv from uv.lock
	uv sync

.PHONY: dev
dev: port-free-8390  ## Run local development server in subprocess mode (port: 8390, IS_DEV=true)
	IS_DEV=true USE_CELERY=false uv run python -m fastapi dev src/app.py --port 8390

.PHONY: dev-mq
dev-mq: port-free-8390 redis-start celery-worker-start celery-flower-start  ## Run local development with Celery (MQ) - starts Redis + Worker + Flower + Web (port: 8390, IS_DEV=true)
	@echo "✅ Redis running on localhost:6379"
	@echo "✅ Celery worker running in background"
	@echo "✅ Flower monitoring UI running at http://localhost:5555"
	@echo "🚀 Starting FastAPI development server on port 8390..."
	@echo "⚠️  Press Ctrl+C to stop. Then run 'make dev-mq-stop' to cleanup."
	IS_DEV=true USE_CELERY=true uv run python -m fastapi dev src/app.py --port 8390

.PHONY: dev-mq-stop
dev-mq-stop: celery-flower-stop celery-worker-stop  ## Stop Celery (MQ) development services (Worker + Flower only, Redis stays running)
	@echo "✅ Celery services stopped"
	@echo "INFO: Redis still running, use 'make redis-stop' to stop it"

.PHONY: run
run: port-free-8391  ## Run local production server in subprocess mode (port: 8391, IS_DEV=false)
	USE_CELERY=false uv run python -m fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: run-mq
run-mq: port-free-8391 redis-start celery-worker-start celery-flower-start  ## Run local production with Celery (MQ) - starts Redis + Worker + Flower + Web (port: 8391, IS_DEV=false)
	@echo "✅ Redis running on localhost:6379"
	@echo "✅ Celery worker running in background"
	@echo "✅ Flower monitoring UI running at http://localhost:5555"
	@echo "🚀 Starting FastAPI production server on port 8391..."
	@echo "⚠️  Press Ctrl+C to stop. Then run 'make run-mq-stop' to cleanup."
	USE_CELERY=true uv run python -m fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: run-mq-stop
run-mq-stop: celery-flower-stop celery-worker-stop  ## Stop Celery (MQ) production services (Worker + Flower only, Redis stays running)
	@echo "✅ Celery services stopped"
	@echo "INFO: Redis still running, use 'make redis-stop' to stop it"

.PHONY: redis-start
redis-start:  ## Start local Redis server
	@if redis-cli -h 127.0.0.1 -p 6379 ping > /dev/null 2>&1; then \
		echo "✅ Redis already running"; \
	else \
		echo "🚀 Starting local Redis server..."; \
		redis-server --daemonize yes; \
		sleep 1; \
	fi

.PHONY: redis-stop
redis-stop:  ## Stop local Redis server
	@if redis-cli -h 127.0.0.1 -p 6379 ping > /dev/null 2>&1; then \
		echo "🛑 Stopping Redis server..."; \
		redis-cli -h 127.0.0.1 -p 6379 shutdown; \
	else \
		echo "ℹ️  Redis not running"; \
	fi

.PHONY: celery-worker-start
celery-worker-start:  ## Start Celery worker in background (pool: CELERY_POOL, default threads)
	@if [ -f ${WORKER_PID_FILE} ] && kill -0 $$(cat ${WORKER_PID_FILE}) 2>/dev/null; then \
		echo "✅ Celery worker already running (PID: $$(cat ${WORKER_PID_FILE}))"; \
	else \
		echo "🚀 Starting Celery worker in background..."; \
		cd src && PYTHONPATH=. uv run sh -c 'celery -A telegramBot.tasks worker --loglevel=info --pool=$${CELERY_POOL:-threads} --pidfile=../${WORKER_PID_FILE} --detach'; \
	fi

.PHONY: celery-worker-stop
celery-worker-stop:  ## Stop Celery worker
	@if [ -f ${WORKER_PID_FILE} ] && kill -0 $$(cat ${WORKER_PID_FILE}) 2>/dev/null; then \
		echo "🛑 Stopping Celery worker (PID: $$(cat ${WORKER_PID_FILE}))..."; \
		kill $$(cat ${WORKER_PID_FILE}); \
		rm -f ${WORKER_PID_FILE}; \
	else \
		echo "ℹ️  Celery worker not running"; \
		rm -f ${WORKER_PID_FILE}; \
	fi

.PHONY: celery-flower-start
celery-flower-start:  ## Start Flower monitoring UI in background
	@if [ -f ${FLOWER_PID_FILE} ] && kill -0 $$(cat ${FLOWER_PID_FILE}) 2>/dev/null; then \
		echo "✅ Flower already running (PID: $$(cat ${FLOWER_PID_FILE}))"; \
	elif $(call PORT_IN_USE,5555); then \
		echo "⚠️  Port 5555 is already in use (Docker Compose flower?) - skipping local Flower"; \
	else \
		echo "🌸 Starting Flower monitoring UI in background on http://localhost:5555..."; \
		nohup bash -c "cd src && PYTHONPATH=. uv run celery -A telegramBot.tasks flower" > /dev/null 2>&1 & \
		echo $$! > ${FLOWER_PID_FILE}; \
		sleep 1; \
	fi

.PHONY: celery-flower-stop
celery-flower-stop:  ## Stop Flower monitoring UI
	@if [ -f ${FLOWER_PID_FILE} ] && kill -0 $$(cat ${FLOWER_PID_FILE}) 2>/dev/null; then \
		echo "🛑 Stopping Flower (PID: $$(cat ${FLOWER_PID_FILE}))..."; \
		kill $$(cat ${FLOWER_PID_FILE}); \
		rm -f ${FLOWER_PID_FILE}; \
	else \
		echo "ℹ️  Flower not running"; \
		rm -f ${FLOWER_PID_FILE}; \
	fi

.PHONY: korail-login-check
korail-login-check:  ## Diagnose ADMIN_KORAIL_ID/PW login inside Docker (shows server response, never the password; counts as 1 login attempt)
	@svc=$$(docker compose ps --status running --services 2>/dev/null | grep -xE 'web_celery|web' | head -1); \
	if [ -n "$$svc" ]; then \
		docker compose exec -T $$svc python - < scripts/check_korail_login.py; \
	else \
		docker compose run --rm --no-deps -T web_celery python - < scripts/check_korail_login.py; \
	fi

.PHONY: lint
lint:	## Run lint
	uv run black .

.PHONY: webapp-install
webapp-install:	## Install web app (PWA) dependencies
	cd webapp && npm ci

.PHONY: webapp-dev
webapp-dev:	## Run web app dev server (http://localhost:5173/app/, proxies /api to port 8390)
	cd webapp && npm run dev

.PHONY: webapp-build
webapp-build:	## Build web app into webapp/dist (served by FastAPI at /app)
	cd webapp && npm ci && npm run build

.PHONY: vapid-keys
vapid-keys:	## Generate VAPID keys for Web Push notifications
	@cd src && uv run python -m core.vapid

.PHONY: test
test:	## Run all tests
	uv run pytest

.PHONY: test-unit
test-unit:	## Run unit tests only
	uv run pytest -m unit -v

.PHONY: test-integration
test-integration:	## Run integration tests only
	uv run pytest -m integration -v

.PHONY: test-e2e
test-e2e:	## Run end-to-end tests only
	uv run pytest -m e2e -v

.PHONY: test-subprocess
test-subprocess:	## Run subprocess mode tests
	uv run pytest -m subprocess -v

.PHONY: test-mq
test-mq:	## Run Celery (MQ) mode tests (requires Redis)
	uv run pytest -m "celery or requires_redis" -v

.PHONY: test-fast
test-fast:	## Run fast tests only (skip slow E2E tests)
	uv run pytest -m "not slow" -v

.PHONY: test-coverage
test-coverage:	## Run tests with coverage report
	uv run pytest --cov=src --cov-report=html --cov-report=term-missing --cov-report=xml

.PHONY: test-verbose
test-verbose:	## Run tests with verbose output
	uv run pytest -vv -s

.PHONY: test-watch
test-watch:	## Run tests in watch mode (requires pytest-watch)
	uv run ptw

.PHONY: coverage-html
coverage-html:	## Generate HTML coverage report and open in browser
	uv run pytest --cov=src --cov-report=html
	@echo "Opening coverage report..."
	@which xdg-open > /dev/null && xdg-open htmlcov/index.html || open htmlcov/index.html || echo "Please open htmlcov/index.html manually"

.PHONY: docker-build
docker-build:	## Build Docker image (includes web app build)
	docker build -t ${IMAGE_NAME} .

.PHONY: docker-push
docker-push:  	## Publish Docker Image
	docker push ${IMAGE_NAME}

# Docker Compose 실행: 바뀐 컨테이너만 다시 만든다 (up -d --build 의 기본 동작)
# 1. 이번 모드에서 쓰지 않는 서비스(반대 모드 전용)만 내림
#    -> subprocess <-> celery 전환 시 8391 등 포트 충돌 방지
# 2. 포트 확인: 이번 모드의 컨테이너가 쓰고 있는 포트는 통과, 다른 프로세스(로컬 서버 등)가 쓰면 중단
# $(1): 띄울 프로필, $(2): 반대 프로필, $(3): 확인할 "서비스:포트" 목록
define COMPOSE_UP
	@unused=$$(docker compose --profile $(2) config --services | grep -vxF "$$(docker compose --profile $(1) config --services)"); \
	if [ -n "$$unused" ] && [ -n "$$(docker compose --profile $(1) --profile $(2) ps -q $$unused)" ]; then \
		echo "🛑 Removing services not used in $(1) mode:" $$unused; \
		docker compose --profile $(1) --profile $(2) rm -sf $$unused; \
	fi
	@for pair in $(3); do \
		svc=$${pair%%:*}; port=$${pair##*:}; \
		if [ -z "$$(docker compose --profile $(1) ps -q --status running $$svc)" ] && $(call PORT_IN_USE,$$port); then \
			echo "❌ 포트 $$port 을(를) Docker Compose 밖의 프로세스가 사용 중입니다. 로컬 서버(make run 등)를 먼저 종료하세요."; \
			exit 1; \
		fi; \
	done
	docker compose --profile $(1) up -d --build --remove-orphans
endef

# BuildKit 이 기본으로 붙이는 provenance 증명에는 빌드마다 다른 값이 들어가, 코드가 같아도 이미지 ID 가
# 바뀌어 compose 가 앱 컨테이너를 매번 다시 만든다. 끄면 이미지가 같을 때 컨테이너를 그대로 둔다.
# (docker-compose.yml 의 build.provenance: false 는 compose v5 에서 적용되지 않아 환경변수로 지정)
docker-compose-up docker-compose-up-mq: export BUILDX_NO_DEFAULT_ATTESTATIONS := 1

.PHONY: docker-compose-up
docker-compose-up:	## Start/update Docker Compose in subprocess mode (recreates only changed containers)
	$(call COMPOSE_UP,subprocess,celery,web:8391)

.PHONY: docker-compose-up-mq
docker-compose-up-mq:	## Start/update Docker Compose in Celery/MQ mode - RECOMMENDED for production (recreates only changed containers)
	$(call COMPOSE_UP,celery,subprocess,web_celery:8391 flower:5555)

.PHONY: docker-compose-down
docker-compose-down:	## Stop all Docker Compose services
	docker compose --profile subprocess --profile celery down --remove-orphans

.PHONY: docker-compose-logs
docker-compose-logs:	## Show logs from all running Docker Compose services
	docker compose logs -f
