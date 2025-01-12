include .env
IMAGE_NAME := raonsol/korail_telegrambot:v1

.PHONY: help
help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

.PHONY: install
install:	## Install dependencies
	pipenv install --dev

.PHONY: dev
dev:  ## Run application in development mode
	pipenv run fastapi dev src/app.py --port 8080

.PHONY: run
run:	## Run application
	pipenv run fastapi run src/app.py --host 0.0.0.0 --port 8080

.PHONY: lint
lint:	## Run lint
	pipenv run black .

.PHONY: build
build:		## Build Docker Image
	docker build -t ${IMAGE_NAME} -f ./Dockerfile .

.PHONY: publish
publish:  	## Publish Docker Image
	docker push ${IMAGE_NAME}

.PHONY: run-docker
run-docker:	## Run Docker container
	docker run -d \
		--name korailbot \
		--restart unless-stopped \
		-e TZ=Asia/Seoul \
		-e USERID=${USERID} \
		-e USERPW=${USERPW} \
		-e BOTTOKEN=${BOTTOKEN} \
		-e ALLOW_LIST=${ALLOW_LIST} \
		-e ADMINPW=${ADMINPW} \
		-p 8391:8080 \
		${IMAGE_NAME}