include .env
IMAGE_NAME := raonsol/korail_telegrambot:v0.6

.PHONY: help
help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: setup-pipenv
setup-pipenv:  ## Install pipenv globally
	pip install --user pipenv --break-system-packages

.PHONY: install
install:	## Install dependencies
	pipenv install --dev

.PHONY: dev
dev:  ## Run application in development mode (port: 8390)
	pipenv run fastapi dev src/app.py --host 0.0.0.0 --port 8391

.PHONY: run
run:	## Run application (port:8391)
	pipenv run fastapi run src/app.py --host 0.0.0.0 --port 8391

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
docker-run:	## Run Docker container
	docker run -d \
		--name korailbot \
		--restart unless-stopped \
		-e TZ=Asia/Seoul \
		-e USERID=${USERID} \
		-e USERPW=${USERPW} \
		-e BOTTOKEN=${BOTTOKEN} \
		-e ALLOW_LIST=${ALLOW_LIST} \
		-e ADMINPW=${ADMINPW} \
		-p 8391:8391 \
		${IMAGE_NAME}