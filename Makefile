include .env
export

IMAGE_NAME := raonsol/korail_telegrambot:v0.7
WORKER_PID_FILE := .celery-worker.pid
FLOWER_PID_FILE := .celery-flower.pid

.PHONY: help
help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: setup-pipenv
setup-pipenv:  ## Install pipenv globally
	pip install --user pipenv --break-system-packages

.PHONY: install
install:	## Install dependencies and create virtual environment
	pipenv install --dev

.PHONY: dev
dev:  ## Run local development server in subprocess mode (port: 8390, IS_DEV=true)
	PIPENV_DONT_LOAD_ENV=1 IS_DEV=true USE_CELERY=false pipenv run python -m fastapi dev src/app.py --port 8390

.PHONY: dev-celery
dev-celery: redis-start celery-worker-start celery-flower-start  ## Run local development with Celery - starts Redis + Worker + Flower + Web (port: 8390, IS_DEV=true)
	@echo "✅ Redis running on localhost:6379"
	@echo "✅ Celery worker running in background"
	@echo "✅ Flower monitoring UI running at http://localhost:5555"
	@echo "🚀 Starting FastAPI development server on port 8390..."
	@echo "⚠️  Press Ctrl+C to stop. Then run 'make dev-celery-stop' to cleanup."
	PIPENV_DONT_LOAD_ENV=1 IS_DEV=true USE_CELERY=true pipenv run python -m fastapi dev src/app.py --port 8390

.PHONY: dev-celery-stop
dev-celery-stop: celery-flower-stop celery-worker-stop  ## Stop Celery development services (Worker + Flower only, Redis stays running)
	@echo "✅ Celery services stopped"
	@echo "INFO: Redis still running, use 'make redis-stop' to stop it"

.PHONY: run
run:  ## Run local production server in subprocess mode (port: 8391, IS_DEV=false)
	PIPENV_DONT_LOAD_ENV=1 USE_CELERY=false pipenv run python -m fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: run-celery
run-celery: redis-start celery-worker-start celery-flower-start  ## Run local production with Celery - starts Redis + Worker + Flower + Web (port: 8391, IS_DEV=false)
	@echo "✅ Redis running on localhost:6379"
	@echo "✅ Celery worker running in background"
	@echo "✅ Flower monitoring UI running at http://localhost:5555"
	@echo "🚀 Starting FastAPI production server on port 8391..."
	@echo "⚠️  Press Ctrl+C to stop. Then run 'make run-celery-stop' to cleanup."
	PIPENV_DONT_LOAD_ENV=1 USE_CELERY=true pipenv run python -m fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: run-celery-stop
run-celery-stop: celery-flower-stop celery-worker-stop  ## Stop Celery production services (Worker + Flower only, Redis stays running)
	@echo "✅ Celery services stopped"
	@echo "INFO: Redis still running, use 'make redis-stop' to stop it"

.PHONY: redis-start
redis-start:  ## Start local Redis server
	@if pgrep -x redis-server > /dev/null; then \
		echo "✅ Redis already running"; \
	else \
		echo "🚀 Starting local Redis server..."; \
		redis-server --daemonize yes; \
		sleep 1; \
	fi

.PHONY: redis-stop
redis-stop:  ## Stop local Redis server
	@if pgrep -x redis-server > /dev/null; then \
		echo "🛑 Stopping Redis server..."; \
		redis-cli shutdown; \
	else \
		echo "ℹ️  Redis not running"; \
	fi

.PHONY: celery-worker-start
celery-worker-start:  ## Start Celery worker in background
	@if [ -f ${WORKER_PID_FILE} ] && kill -0 $$(cat ${WORKER_PID_FILE}) 2>/dev/null; then \
		echo "✅ Celery worker already running (PID: $$(cat ${WORKER_PID_FILE}))"; \
	else \
		echo "🚀 Starting Celery worker in background..."; \
		cd src && PYTHONPATH=. pipenv run celery -A telegramBot.tasks worker --loglevel=info --pidfile=../${WORKER_PID_FILE} --detach; \
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
	else \
		echo "🌸 Starting Flower monitoring UI in background on http://localhost:5555..."; \
		nohup bash -c "cd src && PYTHONPATH=. pipenv run celery -A telegramBot.tasks flower" > /dev/null 2>&1 & \
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

.PHONY: lint
lint:	## Run lint
	pipenv run black .

.PHONY: test
test:	## Run all tests
	pipenv run pytest

.PHONY: test-unit
test-unit:	## Run unit tests only
	pipenv run pytest -m unit -v

.PHONY: test-integration
test-integration:	## Run integration tests only
	pipenv run pytest -m integration -v

.PHONY: test-e2e
test-e2e:	## Run end-to-end tests only
	pipenv run pytest -m e2e -v

.PHONY: test-subprocess
test-subprocess:	## Run subprocess mode tests
	pipenv run pytest -m subprocess -v

.PHONY: test-celery
test-celery:	## Run Celery mode tests (requires Redis)
	pipenv run pytest -m celery -v

.PHONY: test-fast
test-fast:	## Run fast tests only (skip slow E2E tests)
	pipenv run pytest -m "not slow" -v

.PHONY: test-coverage
test-coverage:	## Run tests with coverage report
	pipenv run pytest --cov=src --cov-report=html --cov-report=term-missing --cov-report=xml

.PHONY: test-verbose
test-verbose:	## Run tests with verbose output
	pipenv run pytest -vv -s

.PHONY: test-watch
test-watch:	## Run tests in watch mode (requires pytest-watch)
	pipenv run ptw

.PHONY: coverage-html
coverage-html:	## Generate HTML coverage report and open in browser
	pipenv run pytest --cov=src --cov-report=html
	@echo "Opening coverage report..."
	@which xdg-open > /dev/null && xdg-open htmlcov/index.html || open htmlcov/index.html || echo "Please open htmlcov/index.html manually"

.PHONY: docker-build
docker-build:		## Build Docker Image
	docker build -t ${IMAGE_NAME} -f ./Dockerfile .

.PHONY: docker-push
docker-push:  	## Publish Docker Image
	docker push ${IMAGE_NAME}

.PHONY: docker-compose-up
docker-compose-up:	## Start all services with Docker Compose (subprocess mode)
	docker compose --profile subprocess up -d

.PHONY: docker-compose-up-celery
docker-compose-up-celery:	## Start all services with Docker Compose (Celery mode - RECOMMENDED for production)
	docker compose --profile celery up -d

.PHONY: docker-compose-down
docker-compose-down:	## Stop all Docker Compose services
	docker compose --profile subprocess --profile celery down

.PHONY: docker-compose-logs
docker-compose-logs:	## Show logs from all running Docker Compose services
	docker compose logs -f
