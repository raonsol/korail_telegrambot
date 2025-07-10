include .env
IMAGE_NAME := raonsol/korail_telegrambot:v0.6

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
dev:  ## Run application in development mode (port: 8390)
	USE_CELERY=false pipenv run fastapi dev src/app.py --port 8390

.PHONY: dev-celery
dev-celery:  ## Run application in development mode with Celery (port: 8390)
	USE_CELERY=true pipenv run fastapi dev src/app.py --port 8390

.PHONY: run
run:	## Run application (port:8391)
	USE_CELERY=false pipenv run fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: run-celery
run-celery:	## Run application with Celery (port:8391)
	USE_CELERY=true pipenv run fastapi run src/app.py --host 0.0.0.0 --port 8391

.PHONY: celery-worker
celery-worker:	## Start Celery worker
	pipenv run celery -A src.telegramBot.tasks worker --loglevel=info

.PHONY: celery-beat
celery-beat:	## Start Celery beat scheduler
	pipenv run celery -A src.telegramBot.tasks beat --loglevel=info

.PHONY: celery-flower
celery-flower:	## Start Flower monitoring
	pipenv run celery -A src.telegramBot.tasks flower

.PHONY: lint
lint:	## Run lint
	pipenv run black .

.PHONY: docker-build
docker-build:		## Build Docker Image
	docker build -t ${IMAGE_NAME} -f ./Dockerfile .

.PHONY: docker-push
docker-push:  	## Publish Docker Image
	docker push ${IMAGE_NAME}

.PHONY: docker-run
docker-run:	## Run Docker container (subprocess mode)
	docker run -d \
		--name korailbot \
		--restart unless-stopped \
		-e TZ=Asia/Seoul \
		-e USERID=${USERID} \
		-e USERPW=${USERPW} \
		-e BOTTOKEN=${BOTTOKEN} \
		-e ALLOW_LIST=${ALLOW_LIST} \
		-e ADMINPW=${ADMINPW} \
		-e WEBHOOK_URL=${WEBHOOK_URL} \
		-e USE_CELERY=false \
		-p 8391:8391 \
		${IMAGE_NAME}

.PHONY: docker-run-celery
docker-run-celery:	## Run Docker container (Celery mode)
	docker run -d \
		--name korailbot \
		--restart unless-stopped \
		-e TZ=Asia/Seoul \
		-e USERID=${USERID} \
		-e USERPW=${USERPW} \
		-e BOTTOKEN=${BOTTOKEN} \
		-e ALLOW_LIST=${ALLOW_LIST} \
		-e ADMINPW=${ADMINPW} \
		-e WEBHOOK_URL=${WEBHOOK_URL} \
		-e USE_CELERY=true \
		-p 8391:8391 \
		${IMAGE_NAME}

.PHONY: docker-compose-up
docker-compose-up:	## Start all services with Docker Compose (subprocess mode)
	docker compose --profile subprocess up -d

.PHONY: docker-compose-up-celery
docker-compose-up-celery:	## Start all services with Docker Compose (Celery enabled)
	USE_CELERY=true docker compose --profile celery up -d

.PHONY: docker-compose-dev
docker-compose-dev:	## Start all services in dev mode (port 8390, subprocess mode)
	USE_CELERY=false docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile subprocess up -d

.PHONY: docker-compose-dev-celery
docker-compose-dev-celery:	## Start all services in dev mode with Celery (port 8390)
	USE_CELERY=true docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile celery up -d

.PHONY: docker-compose-down
docker-compose-down:	## Stop all Docker Compose services
	docker compose --profile subprocess --profile celery down

.PHONY: docker-compose-down-dev
docker-compose-down-dev:	## Stop all dev Docker Compose services
	docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile subprocess --profile celery down